"""Independent public calendar health probe and bounded same-repo recovery.

The Runner copy is read-only. Only the public calendar repository may dispatch
its own calendar-sync workflow, once, without strategy, mail or ledger access.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timedelta
import hashlib
import json
import os
import re
import time
import urllib.request
from zoneinfo import ZoneInfo
try:
    from .calendar_runtime import validate
except ImportError:
    from calendar_runtime import validate

REPO = 'chaonpu/ziwuxilv'
WORKFLOW = 'calendar-sync.yml'
ACTIVE = {'queued', 'in_progress', 'waiting', 'pending', 'requested'}
MAX_BYTES = 2_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('calendar_watchdog_redirect_rejected')


def request(path, *, method='GET', body=None, token=''):
    prefix = '/repos/' + REPO + '/'
    if not path.startswith(prefix) or any(x in path for x in ('..', '//', ' ')):
        raise ValueError('calendar_watchdog_path_invalid')
    allowed = (prefix + 'actions/workflows/' + WORKFLOW + '/dispatches')
    if method != 'GET' and (method != 'POST' or path != allowed):
        raise ValueError('calendar_watchdog_mutation_disallowed')
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'ziwuxilv-calendar-watchdog/1'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request('https://api.github.com' + path, method=method,
                                 headers=headers, data=None if body is None else json.dumps(body).encode())
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('calendar_watchdog_response_oversized')
    return json.loads(raw) if raw else {}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('calendar_health_timestamp_missing')
    instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if instant.tzinfo is None:
        raise ValueError('calendar_health_timestamp_ambiguous')
    return instant


def age_hours(value, now):
    try:
        age = (now - timestamp(value)).total_seconds() / 3600
        return age if age >= -5 / 60 else None
    except (TypeError, ValueError):
        return None


def assess(snapshot, health, runs, now):
    if now.tzinfo is None or not isinstance(runs, list) or not all(isinstance(r, dict) for r in runs):
        raise ValueError('calendar_watchdog_inputs_invalid')
    if health.get('schemaVersion') != 1 or not isinstance(health.get('calendars'), dict):
        raise ValueError('calendar_health_schema_invalid')
    if health.get('activeRevision') != snapshot['revision']:
        raise ValueError('calendar_health_revision_mismatch')
    calendars = []
    stale = []
    for key, cal in sorted(snapshot['data']['calendars'].items()):
        record = health['calendars'].get(key, {})
        if not isinstance(record, dict):
            raise ValueError('calendar_health_record_invalid')
        automatic = record.get('adapter') != 'source-monitor' and not record.get('adapter','').startswith('blocked-') and record.get('capability') not in ('blocked','awaiting_live_validation','awaiting_source_consensus')
        daily = now.month >= 10 and str(now.year + 1) not in cal['years'] and automatic
        limit = 48 if daily else 192  # 2 days / 8 days, allowing scheduling jitter
        age = age_hours(record.get('lastAttemptAt'), now)
        overdue = age is None or age > limit
        if overdue:
            stale.append(key)
        end = now.year
        if str(end) in cal['years']:
            while str(end + 1) in cal['years']:
                end += 1
            remaining = (datetime(end, 12, 31, tzinfo=now.tzinfo).date() - now.date()).days
        else:
            remaining = -1
        calendars.append({'calendarId': key, 'checkOverdue': overdue, 'limitHours': limit,
                          'checkAgeHours': None if age is None else round(max(0, age), 2),
                          'sourceStatus': record.get('status', 'not_checked'),
                          'adapter': record.get('adapter', 'unknown'),
                          'lastSourceSuccessAt': record.get('lastSourceSuccessAt'),
                          'lastVerifiedAt': record.get('lastVerifiedAt'),
                          'knownUnknownSessions': sum(day>=now.date().isoformat() for r in cal['years'].values() for day in r['unknownDates']),
                          'coverageDaysRemaining': remaining,
                          'coverageLevel': 'critical' if remaining <= 7 else 'warning' if remaining <= 90 else 'ok'})
    valid_runs = [r for r in runs if r.get('head_branch') == 'main'
                  and r.get('path', '').split('@')[0] == '.github/workflows/' + WORKFLOW]
    active = [r for r in valid_runs if r.get('status') in ACTIVE]
    successes = [r for r in valid_runs if r.get('status') == 'completed' and r.get('conclusion') == 'success'
                 and age_hours(r.get('updated_at'), now) is not None]
    last_success = min(successes, key=lambda r: age_hours(r['updated_at'], now), default=None)
    workflow_age = age_hours(last_success['updated_at'], now) if last_success else None
    limit = min(c['limitHours'] for c in calendars)
    workflow_overdue = workflow_age is None or workflow_age > limit
    dispatched = [r for r in valid_runs if r.get('event') == 'workflow_dispatch'
                  and (age_hours(r.get('created_at'), now) is None
                       or age_hours(r['created_at'], now) < 24)]
    needs_recovery = bool(stale) or workflow_overdue
    decision = ('active_run_no_duplicate' if active else 'dispatch_cooldown' if dispatched and needs_recovery
                else 'recovery_due' if needs_recovery else 'within_cadence')
    attention = any(c['checkOverdue'] or c['coverageLevel'] != 'ok'
                    or c['sourceStatus'] in ('held_preserving_active_calendar', 'not_checked')
                    or c['adapter'] == 'source-monitor' or c['knownUnknownSessions'] for c in calendars)
    stuck = [r['id'] for r in active if age_hours(r.get('created_at'), now) is None
             or age_hours(r['created_at'], now) > 0.5]
    return {'status': 'attention' if attention or needs_recovery or stuck else 'healthy',
            'checkedAt': now.isoformat(timespec='seconds'), 'activeRevision': snapshot['revision'],
            'calendars': calendars, 'overdueCalendars': stale, 'workflowOverdue': workflow_overdue,
            'lastSuccessfulRunId': last_success['id'] if last_success else None,
            'activeRunIds': [r['id'] for r in active], 'stuckRunIds': stuck,
            'recoveryDecision': decision, 'dispatched': 0}


def content(read, path, ref):
    envelope = read('/repos/' + REPO + '/contents/' + path + '?ref=' + ref)
    if envelope.get('path') != path or envelope.get('encoding') != 'base64' or envelope.get('type') != 'file':
        raise ValueError('calendar_watchdog_content_identity_invalid')
    raw = base64.b64decode(envelope['content'].replace('\n', ''), validate=True)
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if len(raw) > 1_000_000 or blob != envelope.get('sha'):
        raise ValueError('calendar_watchdog_content_digest_invalid')
    return raw


def inspect_remote(read=request, now=None):
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    commit = read('/repos/' + REPO + '/commits/main').get('sha', '')
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('calendar_watchdog_commit_invalid')
    index = json.loads(content(read, 'calendars/current.json', commit))
    revision = index.get('sha256', '')
    path = 'calendars/releases/' + revision + '.json'
    if index.get('schemaVersion') != 1 or not re.fullmatch('[0-9a-f]{64}', revision) or index.get('path') != path:
        raise ValueError('calendar_watchdog_index_invalid')
    snapshot = validate(content(read, path, commit))
    if snapshot['revision'] != revision:
        raise ValueError('calendar_watchdog_release_digest_invalid')
    health = json.loads(content(read, 'calendars/health.json', commit))
    response = read('/repos/' + REPO + '/actions/workflows/' + WORKFLOW + '/runs?per_page=100')
    if not isinstance(response.get('workflow_runs'), list):
        raise ValueError('calendar_watchdog_runs_invalid')
    result = assess(snapshot, health, response['workflow_runs'], now)
    result['calendarCommit'] = commit
    return result


def recover(report, env, send=request, *, drill=False):
    # No arbitrary repo, workflow, ref or inputs can be supplied by an event.
    if env.get('GITHUB_REPOSITORY') != REPO or env.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('calendar_recovery_only_from_trusted_public_repository')
    token = env.get('GH_TOKEN', '')
    if not token:
        raise ValueError('calendar_recovery_token_missing')
    decision = report['recoveryDecision']
    if decision != 'recovery_due' and not (drill and decision == 'within_cadence'):
        return report
    # A transport error may mean POST succeeded. Never retry an ambiguous dispatch.
    try:
        response = send('/repos/' + REPO + '/actions/workflows/' + WORKFLOW + '/dispatches',
                        method='POST', body={'ref': 'main', 'return_run_details': True, 'inputs': {'force': False}}, token=token)
    except Exception:
        return {**report, 'recoveryDecision': 'dispatch_response_uncertain_no_retry'}
    rid = response.get('workflow_run_id')
    if type(rid) is not int or rid <= 0:
        return {**report, 'recoveryDecision': 'dispatch_identity_uncertain_no_retry'}
    return {**report, 'recoveryDecision': 'drill_dispatched' if drill else 'recovery_dispatched',
            'dispatched': 1, 'recoveryRunId': rid}


def wait_recovery(run_id, read, *, timeout=780):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = read('/repos/' + REPO + '/actions/runs/' + str(run_id))
        if (run.get('id') != run_id or run.get('head_branch') != 'main'
                or run.get('event') != 'workflow_dispatch' or run.get('run_attempt') != 1
                or run.get('path', '').split('@')[0] != '.github/workflows/' + WORKFLOW):
            raise ValueError('calendar_recovery_run_identity_invalid')
        if run.get('status') == 'completed':
            return run.get('conclusion')
        time.sleep(15)
    return 'pending_timeout_no_redispatch'


def emit(report):
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    if report['status'] != 'healthy':
        print('::warning title=交易日历状态需关注::请查看本次摘要中的来源、覆盖范围与检查时效。')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write('### 交易日历独立监测\n\n状态：' + report['status'] + '；补检：' + report['recoveryDecision'] + '\n\n')
            stream.write('| 日历 | 检查超期 | 来源状态 | 连续覆盖剩余天数 |\n|---|---|---|---|\n')
            for c in report['calendars']:
                stream.write('| ' + c['calendarId'] + ' | ' + ('是' if c['checkOverdue'] else '否') + ' | '
                             + c['sourceStatus'] + ' | ' + str(c['coverageDaysRemaining']) + ' |\n')
            if report.get('recoveryRunId'):
                stream.write('\n补检运行：https://github.com/' + REPO + '/actions/runs/' + str(report['recoveryRunId']) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--recover', action='store_true')
    parser.add_argument('--drill', action='store_true')
    args = parser.parse_args()
    if args.drill and not args.recover:
        parser.error('--drill requires --recover')
    token = os.environ.get('GH_TOKEN', '')
    read = lambda path: request(path, token=token)
    try:
        report = inspect_remote(read)
        if args.recover:
            if args.drill:
                # Deployment drill waits for an existing maintenance run instead
                # of dispatching a duplicate. Scheduled probes never wait here.
                deadline = time.monotonic() + 180
                while report['activeRunIds'] and time.monotonic() < deadline:
                    time.sleep(10)
                    report = inspect_remote(read)
            report = recover(report, os.environ, drill=args.drill)
            if report.get('recoveryRunId'):
                report['recoveryResult'] = wait_recovery(report['recoveryRunId'], read)
                after = inspect_remote(read)
                report['afterRecovery'] = {'recoveryDecision': after['recoveryDecision'],
                                          'overdueCalendars': after['overdueCalendars'], 'status': after['status']}
        emit(report)
        return 2 if ('uncertain' in report['recoveryDecision']
                     or report.get('recoveryResult', 'success') != 'success'
                     or (report['recoveryDecision'] == 'recovery_due' and not args.recover)) else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'unavailable', 'reason': type(exc).__name__,
                          'httpStatus': getattr(exc, 'code', None), 'dispatched': 0}), flush=True)
        print('::error title=交易日历监测不可用::无法确认最新状态；未盲目重试补检。')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
