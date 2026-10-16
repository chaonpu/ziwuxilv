"""Fail-closed Android build routing decision logic; no GitHub API or signing access.

Run `python tools/build_route_policy.py --self-test` for test cases.
Run `python tools/build_route_policy.py scenario.json` to evaluate local metadata.
This module emits *recommendations only*; signing must independently validate
source SHA, full handoff provenance, and the fallback authorization policy.
"""
from __future__ import annotations

import json
import sys
import unittest
from dataclasses import asdict, dataclass
from pathlib import Path

UBUNTU = "ubuntu_test"
WINDOWS = "windows_test"
SIGNER = "signing_repo_fallback"
HOLD = "hold"
USE_HANDOFF = "use_verified_handoff"
VALID_STATES = {"not_started", "queued", "in_progress", "success", "failure"}


@dataclass(frozen=True)
class Decision:
    route: str
    reason: str


def _state(obj, name):
    if not isinstance(obj, dict):
        raise ValueError(f"{name} must be an object")
    state = obj.get("state")
    if state not in VALID_STATES:
        raise ValueError(f"Invalid {name} state: {state!r}")
    return state


def decide(plan):
    """Select a route from verified observations; never equate errors with quota.

    Each `sourceVerified` is a *signal* for preflight only, not signing approval.
    When using SIGNER, a separate private source checksum and test verification
    must run inside the signing repository before producing a release.
    """
    if not isinstance(plan, dict):
        raise ValueError("Expected a JSON object")
    ubuntu = plan.get("ubuntu", {})
    windows = plan.get("windows", {})
    signing = plan.get("signing", {})
    us = _state(ubuntu, "ubuntu")
    ws = _state(windows, "windows")
    if not isinstance(signing, dict):
        raise ValueError("signing must be an object")

    if us == "success":
        if ubuntu.get("verifiedHandoff") is True:
            return Decision(USE_HANDOFF, "Use the exact verified Ubuntu source and artifact.")
        return Decision(HOLD, "Ubuntu reported success but complete handoff validation is missing.")
    if us in {"not_started", "in_progress"}:
        return Decision(UBUNTU if us == "not_started" else HOLD,
                        "Ubuntu is preferred." if us == "not_started" else "Ubuntu is still running.")

    # Runner scheduling/billing failures are not always available as ordinary
    # job conclusions. Require explicit quota evidence before fallback.
    quota_proven = (ubuntu.get("failureKind") == "actions_quota_exhausted"
                    and ubuntu.get("quotaConfirmed") is True
                    and ubuntu.get("testsExecuted") is False)
    if not quota_proven:
        return Decision(HOLD, "Ubuntu quota has not been conclusively established; no fallback.")
    if us == "queued" and ubuntu.get("quotaConfirmed") is not True:
        return Decision(HOLD, "Queued Ubuntu job is not proof of quota exhaustion.")

    if ws == "success":
        if windows.get("verifiedHandoff") is True:
            return Decision(USE_HANDOFF, "Use the exact verified Windows source and artifact.")
        return Decision(HOLD, "Windows reported success without a fully validated handoff.")
    if ws == "in_progress":
        return Decision(HOLD, "Windows build is executing; do not dispatch a duplicate.")
    if ws == "not_started" and windows.get("runnerOnline") is True:
        return Decision(WINDOWS, "Windows self-hosted runner is available.")
    if ws == "queued":
        # A busy runner is not an outage. Absence of runner capacity cannot
        # be inferred solely from queued time without server-side evidence.
        if not (windows.get("runnerOnline") is False
                or (windows.get("runnerBusy") is False
                    and windows.get("queuedMinutes", 0) >= 20
                    and windows.get("heartbeatVerified") is True)):
            return Decision(HOLD, "Windows pending; missing offline or timeout evidence.")
    elif ws == "failure":
        if windows.get("failureKind") not in {
            "runner_offline", "runner_connection_lost",
            "github_network_unreachable", "action_download_timeout",
            "runner_setup_failure"
        } or windows.get("testsFailed") is True:
            return Decision(HOLD, "Windows build/test failure or unclassified failure: never bypass checks.")
    elif ws == "not_started" and windows.get("runnerOnline") is not False:
        return Decision(HOLD, "Windows availability is unknown.")

    if signing.get("sourceVerified") is not True:
        return Decision(HOLD, "Signing fallback requires privately verified exact-commit source.")
    if signing.get("available") is not True:
        return Decision(HOLD, "Signing repository availability and allowance not confirmed.")
    if signing.get("fallbackAuthorized") is not True:
        return Decision(HOLD, "Signing fallback requires an independently authorized incident.")
    return Decision(SIGNER, "Infrastructure failure confirmed; signer must revalidate full source and tests.")


class TestRoutes(unittest.TestCase):
    def base(self):
        return {
            "ubuntu": {"state": "not_started"},
            "windows": {"state": "not_started", "runnerOnline": True},
            "signing": {"available": True, "sourceVerified": True, "fallbackAuthorized": True},
        }

    def test_prefer_ubuntu(self):
        self.assertEqual(decide(self.base()).route, UBUNTU)

    def test_ubuntu_succeeded_use_handoff(self):
        p = self.base()
        p["ubuntu"] = {"state": "success", "verifiedHandoff": True}
        self.assertEqual(decide(p).route, USE_HANDOFF)

    def test_no_quota_evidence_does_not_fallback(self):
        p = self.base()
        p["ubuntu"] = {"state": "failure", "failureKind": "failed_build"}
        self.assertEqual(decide(p).route, HOLD)

    def test_quota_fallback_to_windows(self):
        p = self.base()
        p["ubuntu"] = {"state": "failure", "failureKind": "actions_quota_exhausted",
                       "quotaConfirmed": True, "testsExecuted": False}
        self.assertEqual(decide(p).route, WINDOWS)

    def test_offline_windows_to_signer(self):
        p = self.base()
        p["ubuntu"] = {"state": "queued", "failureKind": "actions_quota_exhausted",
                       "quotaConfirmed": True, "testsExecuted": False}
        p["windows"] = {"state": "not_started", "runnerOnline": False}
        self.assertEqual(decide(p).route, SIGNER)

    def test_windows_setup_failure_to_signer(self):
        p = self.base()
        p["ubuntu"] = {"state": "failure", "failureKind": "actions_quota_exhausted",
                       "quotaConfirmed": True, "testsExecuted": False}
        p["windows"] = {"state": "failure", "failureKind": "action_download_timeout"}
        self.assertEqual(decide(p).route, SIGNER)

    def test_windows_compilation_failure_blocks(self):
        p = self.base()
        p["ubuntu"] = {"state": "failure", "failureKind": "actions_quota_exhausted",
                       "quotaConfirmed": True, "testsExecuted": False}
        p["windows"] = {"state": "failure", "failureKind": "kotlin_compilation_failure",
                        "testsFailed": True}
        self.assertEqual(decide(p).route, HOLD)

    def test_busy_runner_does_not_trigger_signer(self):
        p = self.base()
        p["ubuntu"] = {"state": "queued", "failureKind": "actions_quota_exhausted",
                       "quotaConfirmed": True, "testsExecuted": False}
        p["windows"] = {"state": "queued", "runnerOnline": True,
                        "runnerBusy": True, "queuedMinutes": 30}
        self.assertEqual(decide(p).route, HOLD)

    def test_no_verified_source_cannot_sign(self):
        p = self.base()
        p["ubuntu"] = {"state": "failure", "failureKind": "actions_quota_exhausted",
                       "quotaConfirmed": True, "testsExecuted": False}
        p["windows"] = {"state": "failure", "failureKind": "github_network_unreachable"}
        p["signing"]["sourceVerified"] = False
        self.assertEqual(decide(p).route, HOLD)

    def test_signer_unavailable_cannot_sign(self):
        p = self.base()
        p["ubuntu"] = {"state": "failure", "failureKind": "actions_quota_exhausted",
                       "quotaConfirmed": True, "testsExecuted": False}
        p["windows"] = {"state": "not_started", "runnerOnline": False}
        p["signing"]["available"] = False
        self.assertEqual(decide(p).route, HOLD)

    def test_unknown_runner_is_not_offline(self):
        p = self.base()
        p["ubuntu"] = {"state": "failure", "failureKind": "actions_quota_exhausted",
                       "quotaConfirmed": True, "testsExecuted": False}
        p["windows"]["runnerOnline"] = None
        self.assertEqual(decide(p).route, HOLD)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    elif len(sys.argv) == 2:
        result = decide(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
        print(json.dumps(asdict(result), ensure_ascii=False))
    else:
        raise SystemExit("Usage: build_route_policy.py --self-test | scenario.json")
