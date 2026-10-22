"""Public, bounded calendar snapshots. Shared byte-for-byte with Runner/Stock.

No private credentials, strategy execution or data writes. Each process pins a
single validated snapshot. Missing coverage never becomes a guessed weekday.
"""
from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.request
from zoneinfo import ZoneInfo

REPO = "chaonpu/ziwuxilv"
MAX_BYTES = 1_000_000
HEX = re.compile(r"^[0-9a-f]{64}$")
_snapshot = None


class CalendarUnavailable(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise CalendarUnavailable(reason)


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def validate(raw):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BYTES, "calendar_size_invalid")
    data = json.loads(raw)
    require(data.get("schemaVersion") == 1 and isinstance(data.get("calendars"), dict)
            and {"CN_EQ", "US_EQ"} <= set(data["calendars"]), "calendar_schema_invalid")
    for key, cal in data["calendars"].items():
        require(bool(re.fullmatch(r"[A-Z][A-Z0-9_]{1,40}", key)), "calendar_id_invalid")
        ZoneInfo(cal["timezone"])
        require(isinstance(cal["years"], dict) and bool(cal["years"]), "calendar_years_empty")
        require(isinstance(cal["weekdays"], list) and bool(cal["weekdays"])
                and len(set(cal["weekdays"])) == len(cal["weekdays"])
                and all(type(d) is int and 1 <= d <= 7 for d in cal["weekdays"]), "calendar_weekdays_invalid")
        for year, record in cal["years"].items():
            require(bool(re.fullmatch(r"20[0-9]{2}", year)), "calendar_year_invalid")
            require(record.get("coverageStart") == year + "-01-01"
                    and record.get("coverageEnd") == year + "-12-31"
                    and record.get("verified") is True, "calendar_coverage_invalid")
            require(isinstance(record.get("sources"), list) and bool(record["sources"])
                    and all(isinstance(s, str) and s.startswith("https://") for s in record["sources"]), "calendar_sources_invalid")
            require(isinstance(record.get("holidays"), dict)
                    and isinstance(record.get("overrides"), dict)
                    and isinstance(record.get("unknownDates"), list), "calendar_dates_invalid")
            require(len(set(record["unknownDates"])) == len(record["unknownDates"]), "calendar_duplicate_date")
            groups = [set(record["holidays"]), set(record["overrides"]), set(record["unknownDates"])]
            require(not any(groups[i] & groups[j] for i in range(3) for j in range(i)), "calendar_date_conflict")
            for day in set().union(*groups):
                require(date.fromisoformat(day).isoformat() == day and day.startswith(year + "-"), "calendar_date_invalid")
            for clocks in [cal.get("session", {}), *record["overrides"].values()]:
                require(isinstance(clocks, dict), "calendar_clock_invalid")
                for field, minute in clocks.items():
                    require(field in ("openMinute", "closeMinute", "postCloseMinute", "halfDay")
                            and (type(minute) is int and 0 <= minute <= 1440
                                 if field != "halfDay" else type(minute) is bool), "calendar_clock_invalid")
                if "openMinute" in clocks and "closeMinute" in clocks:
                    require(clocks["openMinute"] < clocks["closeMinute"], "calendar_clock_order")
    return {"revision": hashlib.sha256(raw).hexdigest(), "data": data}


def day_rule(snapshot, calendar_id, day):
    if isinstance(day, str):
        day = date.fromisoformat(day)
    cal = snapshot["data"]["calendars"].get(calendar_id)
    record = cal["years"].get(str(day.year)) if cal else None
    if not record or day.isoformat() in record["unknownDates"]:
        return {"kind": "UNKNOWN", "revision": snapshot["revision"]}
    key = day.isoformat()
    holiday = record["holidays"].get(key)
    return {"kind": "CLOSED" if holiday is not None or day.isoweekday() not in cal["weekdays"] else "NORMAL",
            "holiday": holiday, "timezone": cal["timezone"],
            "revision": snapshot["revision"], **cal.get("session", {}),
            **record["overrides"].get(key, {})}


def adjacent(snapshot, calendar_id, day, direction):
    require(direction in (-1, 1), "calendar_direction_invalid")
    cursor = day
    for _ in range(370):
        cursor += timedelta(days=direction)
        rule = day_rule(snapshot, calendar_id, cursor)
        if rule["kind"] == "UNKNOWN":
            return None
        if rule["kind"] == "NORMAL":
            return cursor
    return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise CalendarUnavailable("calendar_redirect_rejected")


def fetch_public(path):
    require(path == "calendars/current.json" or bool(re.fullmatch(r"calendars/releases/[0-9a-f]{64}\.json", path)), "calendar_path_invalid")
    req = urllib.request.Request("https://api.github.com/repos/" + REPO + "/contents/" + path + "?ref=main",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "ziwuxilv-calendar/1"})
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=12) as response:
        raw = response.read(MAX_BYTES * 2 + 1)
    require(len(raw) <= MAX_BYTES * 2, "calendar_response_oversized")
    envelope = json.loads(raw)
    require(envelope.get("path") == path and envelope.get("encoding") == "base64"
            and envelope.get("type") == "file", "calendar_response_invalid")
    content = base64.b64decode(envelope["content"].replace("\n", ""), validate=True)
    require(len(content) <= MAX_BYTES, "calendar_response_oversized")
    blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    require(blob == envelope.get("sha"), "calendar_git_blob_mismatch")
    return content


def remote(revision="", *, fetch=fetch_public):
    if not revision:
        index = json.loads(fetch("calendars/current.json"))
        require(index.get("schemaVersion") == 1, "calendar_index_invalid")
        revision = index.get("sha256", "")
        require(index.get("path") == "calendars/releases/" + revision + ".json", "calendar_index_path_invalid")
    require(isinstance(revision, str) and bool(HEX.fullmatch(revision)), "calendar_revision_invalid")
    raw = fetch("calendars/releases/" + revision + ".json")
    snapshot = validate(raw)
    require(snapshot["revision"] == revision, "calendar_digest_mismatch")
    return raw, snapshot


def install(raw, target):
    snapshot = validate(raw)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent, prefix="calendar-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
    return snapshot


def seed_path():
    return Path(__file__).resolve().with_name("calendar-seed.json")


def current():
    global _snapshot
    if _snapshot is None:
        path = Path(os.environ.get("ZIWU_CALENDAR_FILE", str(seed_path())))
        _snapshot = validate(path.read_bytes())
        expected = os.environ.get("ZIWU_CALENDAR_REVISION", "")
        require(not expected or expected == _snapshot["revision"], "calendar_pinned_revision_mismatch")
    return _snapshot


def pin_latest(cache, *, revision="", fetch=fetch_public):
    """Refresh once; a requested revision can never fall back to another one."""
    global _snapshot
    cache = Path(cache)
    try:
        raw, snapshot = remote(revision, fetch=fetch)
        install(raw, cache)
        origin = "remote"
    except (OSError, ValueError, KeyError, TypeError):
        candidates = [cache, seed_path()]
        snapshot = None
        for path in candidates:
            try:
                found = validate(path.read_bytes())
                if not revision or found["revision"] == revision:
                    raw, snapshot = path.read_bytes(), found
                    origin = "verified_cache" if path == cache else "bundled"
                    break
            except (OSError, ValueError, KeyError, TypeError):
                continue
        require(snapshot is not None, "calendar_requested_revision_unavailable")
        install(raw, cache)
    _snapshot = snapshot
    os.environ["ZIWU_CALENDAR_FILE"] = str(cache.resolve())
    os.environ["ZIWU_CALENDAR_REVISION"] = snapshot["revision"]
    return {"calendarRevision": snapshot["revision"], "calendarOrigin": origin,
            "calendarFile": str(cache.resolve())}


def trace():
    return {"calendarRevision": current()["revision"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--revision", default=os.environ.get("ZIWU_CALENDAR_REVISION", ""))
    args = parser.parse_args()
    result = pin_latest(args.cache, revision=args.revision)
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write("ZIWU_CALENDAR_FILE=" + result["calendarFile"] + "\nZIWU_CALENDAR_REVISION=" + result["calendarRevision"] + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
