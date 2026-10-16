"""Fail-closed Android build path policy. Recommendation ONLY; never dispatches.

Priority: Test hosted Ubuntu -> private signing repo hosted Ubuntu ->
Test self-hosted Windows (unsigned APK). After Windows builds, an independent
private signing runner is still required for an installable release.

Sample: python tools/build_route_policy.py scenario.json
Tests:  python tools/build_route_policy.py --self-test
"""
from __future__ import annotations

import json
import sys
import unittest
from dataclasses import asdict, dataclass
from pathlib import Path

UBUNTU = "ubuntu_test"
SIGNER = "signing_repo_hosted"
WINDOWS = "windows_test_unsigned"
HOLD = "hold"
USE_HANDOFF = "use_verified_unsigned_handoff"
SIGNED_READY = "use_verified_signed_release"
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


def _confirmed_quota(obj):
    """Do not infer billed quota failure from a failed job or a queued run."""
    if (obj.get("failureKind") != "actions_quota_exhausted"
            or obj.get("quotaConfirmed") is not True
            or obj.get("testsExecuted") is not False):
        return False
    if obj["state"] == "queued":
        evidence = obj.get("quotaEvidence")
        if not isinstance(evidence, dict):
            return False
        used = evidence.get("usedMinutes")
        included = evidence.get("includedMinutes")
        if type(used) not in (float, int) or type(included) not in (float, int):
            return False
        if included <= 0 or used < included:
            return False
    return True


def decide(plan):
    """Return a safe route based on *independently verified* state evidence.

    This policy performs no work and never authorizes signing by itself.
    Every stage must keep the exact source SHA and verify the full handoff.
    """
    if not isinstance(plan, dict):
        raise ValueError("Expected a JSON object")
    ubuntu = plan.get("ubuntu", {})
    signing = plan.get("signing", {})
    windows = plan.get("windows", {})
    us = _state(ubuntu, "ubuntu")
    ss = _state(signing, "signing")
    ws = _state(windows, "windows")

    # Stage 1: Test hosted Ubuntu always has priority.
    if us == "success":
        if ubuntu.get("verifiedHandoff") is True:
            return Decision(USE_HANDOFF,
                            "Test Ubuntu verified unsigned handoff; private signing still required.")
        return Decision(HOLD, "Ubuntu success lacks exact-commit, test and artifact verification.")
    if us == "not_started" and not _confirmed_quota(ubuntu):
        return Decision(UBUNTU, "Use Test hosted Ubuntu first.")
    if us == "in_progress":
        return Decision(HOLD, "Test Ubuntu is running; never start a duplicate build.")
    if not _confirmed_quota(ubuntu):
        return Decision(HOLD, "Test Ubuntu quota exhaustion not proven; other failures do not fall back.")

    # Stage 2: Signing repository hosted Ubuntu (private) only on proven
    # Test hosted-minute exhaustion, with separate private source checks.
    if ss == "success":
        if signing.get("verifiedSignedArtifact") is True:
            return Decision(SIGNED_READY, "Private signing repo produced a verified signed APK.")
        return Decision(HOLD, "Signing success has no verified APK identity/provenance.")
    if ss == "in_progress":
        return Decision(HOLD, "Private signer is running; avoid duplicate dispatch.")
    if ss == "not_started" and not _confirmed_quota(signing):
        if (signing.get("available") is True
                and signing.get("sourceVerified") is True
                and signing.get("fallbackAuthorized") is True):
            return Decision(SIGNER, "Test hosted quota exhausted; use private signer hosted runner.")
        return Decision(HOLD, "Private signer capacity, source or explicit authorization not verified.")
    if not _confirmed_quota(signing):
        return Decision(HOLD, "Signing repository quota not conclusively exhausted; stop on other failures.")

    # Stage 3: Both PRIVATE GitHub-hosted budgets have documented exhaustion.
    # Existing Windows runner is installed only in Test and builds unsigned APK.
    if ws == "success":
        if windows.get("verifiedHandoff") is True:
            return Decision(USE_HANDOFF,
                            "Windows unsigned APK verified; signing is BLOCKED without signer capacity.")
        return Decision(HOLD, "Windows completed but its unsigned handoff is not verified.")
    if ws == "in_progress":
        return Decision(HOLD, "Windows self-hosted build already running.")
    if ws == "not_started":
        if windows.get("runnerOnline") is True:
            return Decision(WINDOWS, "Both private hosted quotas exhausted; use Test Windows for unsigned build.")
        return Decision(HOLD, "Windows runner offline or state unknown; no compute capacity.")
    if ws == "queued":
        return Decision(HOLD, "Windows queued; never confuse busy, offline and failure.")
    return Decision(HOLD, "Windows failed; never bypass compile/test/network errors into a release.")


class TestRoutes(unittest.TestCase):
    def base(self):
        return {
            "ubuntu": {"state": "not_started"},
            "signing": {"state": "not_started", "available": True,
                        "sourceVerified": True, "fallbackAuthorized": True},
            "windows": {"state": "not_started", "runnerOnline": True},
        }

    def exhaust(self, p, stage, state="failure"):
        p[stage] = {"state": state, "failureKind": "actions_quota_exhausted",
                    "quotaConfirmed": True, "testsExecuted": False}
        if state == "queued":
            p[stage]["quotaEvidence"] = {"usedMinutes": 2000, "includedMinutes": 2000}

    def test_prefer_test_ubuntu(self):
        self.assertEqual(decide(self.base()).route, UBUNTU)

    def test_ubuntu_unsigned_verified(self):
        p = self.base()
        p["ubuntu"] = {"state": "success", "verifiedHandoff": True}
        self.assertEqual(decide(p).route, USE_HANDOFF)

    def test_ubuntu_success_without_handoff_holds(self):
        p = self.base()
        p["ubuntu"] = {"state": "success"}
        self.assertEqual(decide(p).route, HOLD)

    def test_test_quota_then_signing_repo(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        self.assertEqual(decide(p).route, SIGNER)

    def test_signing_repo_success_verified(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        p["signing"] = {"state": "success", "verifiedSignedArtifact": True}
        self.assertEqual(decide(p).route, SIGNED_READY)

    def test_both_quotas_then_windows(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        self.exhaust(p, "signing")
        self.assertEqual(decide(p).route, WINDOWS)

    def test_both_queued_quotas_with_evidence(self):
        p = self.base()
        self.exhaust(p, "ubuntu", "queued")
        self.exhaust(p, "signing", "queued")
        self.assertEqual(decide(p).route, WINDOWS)

    def test_queued_without_billing_evidence_blocks(self):
        p = self.base()
        self.exhaust(p, "ubuntu", "queued")
        del p["ubuntu"]["quotaEvidence"]
        self.exhaust(p, "signing")
        self.assertEqual(decide(p).route, HOLD)

    def test_signer_quota_unproven_blocks_windows(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        p["signing"] = {"state": "failure", "failureKind": "billing_unknown"}
        self.assertEqual(decide(p).route, HOLD)

    def test_signer_network_failure_blocks_windows(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        p["signing"] = {"state": "failure", "failureKind": "github_network_unreachable"}
        self.assertEqual(decide(p).route, HOLD)

    def test_signer_compilation_failure_blocks_windows(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        p["signing"] = {"state": "failure", "failureKind": "kotlin_compilation_failure",
                        "testsExecuted": True}
        self.assertEqual(decide(p).route, HOLD)

    def test_test_compilation_failure_never_falls_back(self):
        p = self.base()
        p["ubuntu"] = {"state": "failure", "failureKind": "kotlin_compilation_failure",
                       "testsExecuted": True}
        self.assertEqual(decide(p).route, HOLD)

    def test_no_verified_private_source_cannot_use_signer(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        p["signing"]["sourceVerified"] = False
        self.assertEqual(decide(p).route, HOLD)

    def test_signer_without_quota_or_availability_confirmation_holds(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        p["signing"]["available"] = False
        self.assertEqual(decide(p).route, HOLD)

    def test_windows_offline_when_both_quotas_exhausted(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        self.exhaust(p, "signing")
        p["windows"]["runnerOnline"] = False
        self.assertEqual(decide(p).route, HOLD)

    def test_windows_unsigned_success_cannot_count_as_signed(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        self.exhaust(p, "signing")
        p["windows"] = {"state": "success", "verifiedHandoff": True}
        result = decide(p)
        self.assertEqual(result.route, USE_HANDOFF)
        self.assertIn("signing is BLOCKED", result.reason)

    def test_windows_busy_no_duplicate(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        self.exhaust(p, "signing")
        p["windows"] = {"state": "queued", "runnerOnline": True, "runnerBusy": True}
        self.assertEqual(decide(p).route, HOLD)

    def test_both_quotas_false_boolean_not_numbers(self):
        p = self.base()
        self.exhaust(p, "ubuntu", "queued")
        p["ubuntu"]["quotaEvidence"]["usedMinutes"] = True
        self.exhaust(p, "signing")
        self.assertEqual(decide(p).route, HOLD)

    def test_signing_repo_in_progress_no_windows(self):
        p = self.base()
        self.exhaust(p, "ubuntu")
        p["signing"] = {"state": "in_progress", "available": True}
        self.assertEqual(decide(p).route, HOLD)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    elif len(sys.argv) == 2:
        result = decide(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
        print(json.dumps(asdict(result), ensure_ascii=False))
    else:
        raise SystemExit("Usage: build_route_policy.py --self-test | scenario.json")
