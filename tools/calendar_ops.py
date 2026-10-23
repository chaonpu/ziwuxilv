"""Read-only impact/status reports and explicitly confirmed immutable rollback.

No strategy, mail, credential or ledger mutations. Optional private ledger
inputs are reduced to dates in memory and are never written into public state.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from calendar_runtime import canonical, validate, day_rule, require, install

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ('kind', 'timezone', 'openMinute', 'closeMinute', 'postCloseMinute', 'halfDay')
HEX = re.compile(r'[0-9a-f]{64}')


def today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def snapshot(root, revision=None, candidate=False):
    index = json.loads((root/'calendars/current.json').read_bytes()) if revision is None else None
    revision = index.get('sha256', '') if index is not None else revision
    require(isinstance(revision, str) and HEX.fullmatch(revision), 'ops_revision_invalid')
    path = 'calendars/releases/'+revision+'.json'
    require(index is None or index.get('schemaVersion') == 1 and index.get('path') == path, 'ops_index_invalid')
    target = root/path
    if candidate and not target.exists():target = root/'calendars/candidates'/ (revision+'.json')
    raw = target.read_bytes()
    result = validate(raw)
    require(result['revision'] == revision, 'ops_snapshot_digest_mismatch')
    return raw, result


def semantics(value):
    return {key:value[key] for key in FIELDS if key in value}


def impact(before, after, cutoff=None, ledger_dates=None):
    cutoff = cutoff or today()
    changes = []; counts = {}; historical = 0; matched = set()
    calendars = before['data']['calendars'].keys() | after['data']['calendars'].keys()
    for key in sorted(calendars):
        old = before['data']['calendars'].get(key, {}); new = after['data']['calendars'].get(key, {})
        years = set(old.get('years', {})) | set(new.get('years', {}))
        for year in sorted(years):
            cursor = date(int(year), 1, 1)
            while cursor.year == int(year):
                a = semantics(day_rule(before, key, cursor)); b = semantics(day_rule(after, key, cursor))
                if a != b:
                    entry = {'calendarId':key, 'date':cursor.isoformat(), 'before':a, 'after':b,
                             'historical':cursor <= cutoff}
                    changes.append(entry); counts[key] = counts.get(key, 0)+1
                    historical += int(cursor <= cutoff)
                    if ledger_dates is not None and cursor.isoformat() in ledger_dates and cursor <= cutoff:
                        matched.add((key, cursor.isoformat()))
                cursor += timedelta(days=1)
    return {'schemaVersion':1, 'fromRevision':before['revision'], 'toRevision':after['revision'],
            'asOf':cutoff.isoformat(), 'changedDays':len(changes), 'historicalChangedDays':historical,
            'byCalendar':counts, 'affectedLedgerCalendarDates':len(matched) if ledger_dates is not None else None,
            'ledgerExamined':ledger_dates is not None, 'ledgerWrites':0, 'changes':changes}


def ledger_dates(folder):
    """Explicit private local input only; never expose records, symbols or values."""
    root = Path(folder); require(root.is_dir() and not root.is_symlink(), 'ops_ledger_directory_invalid')
    files = sorted(p for p in root.rglob('*.json') if not p.is_symlink())
    require(len(files) <= 2000, 'ops_ledger_file_count_exceeded')
    result = set()
    def collect(value, depth=0):
        require(depth <= 50, 'ops_ledger_depth_exceeded')
        if isinstance(value, dict):
            for key, item in value.items():
                if re.fullmatch(r'20\d{2}-\d{2}-\d{2}', key):result.add(date.fromisoformat(key).isoformat())
                collect(item, depth+1)
        elif isinstance(value, list):
            for item in value:collect(item, depth+1)
        elif isinstance(value, str) and re.fullmatch(r'20\d{2}-\d{2}-\d{2}(?:T.*)?', value):
            result.add(date.fromisoformat(value[:10]).isoformat())
    for path in files:
        require(path.resolve().is_relative_to(root.resolve()) and path.stat().st_size <= 10_000_000, 'ops_ledger_file_invalid')
        collect(json.loads(path.read_bytes()))
    return result


def rollback_plan(root, target, expected, reason, cutoff=None):
    require(isinstance(reason, str) and 8 <= len(reason.strip()) <= 300, 'ops_rollback_reason_required')
    _, active = snapshot(root)
    require(active['revision'] == expected, 'ops_rollback_current_revision_changed')
    require(target != expected, 'ops_rollback_same_revision')
    _, previous = snapshot(root, target)
    assessment = impact(active, previous, cutoff)
    plan = {'schemaVersion':1, 'operation':'rollback', 'fromRevision':expected, 'toRevision':target,
            'reason':reason.strip(), 'reviewDate':assessment['asOf'],
            'impactSha256':hashlib.sha256(canonical(assessment)).hexdigest(),
            'changedDays':assessment['changedDays'], 'historicalChangedDays':assessment['historicalChangedDays'],
            'ledgerWrites':0}
    return {**plan, 'confirmationSha256':hashlib.sha256(canonical(plan)).hexdigest()}


def record(root, kind, revision, details=None, now=None, run_id=None):
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    require(now.tzinfo is not None, 'ops_observation_time_ambiguous')
    path = root/'calendars/operations/history.json'
    history = json.loads(path.read_bytes()) if path.exists() else {'schemaVersion':1, 'events':[]}
    require(history.get('schemaVersion') == 1 and isinstance(history.get('events'), list), 'ops_history_invalid')
    run_id = run_id or os.environ.get('GITHUB_RUN_ID', '')
    if run_id:
        require(str(run_id).isdigit(), 'ops_observation_run_invalid')
        event = {'at':now.isoformat(timespec='seconds'), 'kind':kind, 'revision':revision, 'runId':str(run_id),
                 **(details or {})}
        if not any(e.get('runId') == event['runId'] and e.get('kind') == kind for e in history['events']):
            history['events'].append(event)
        history['events'] = history['events'][-730:]
        install_json(history, path)
    return history


def install_json(value, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.tmp');temp.write_bytes(canonical(value));temp.replace(path)


def coverage(snapshot, cutoff):
    result=[]
    for key,cal in sorted(snapshot['data']['calendars'].items()):
        end=cutoff.year
        if str(end) not in cal['years']:
            result.append({'calendarId':key,'level':'critical','reason':'current_year_not_covered'});continue
        while str(end+1) in cal['years']:end+=1
        days=(date(end,12,31)-cutoff).days
        if days <= 90:result.append({'calendarId':key,'level':'critical' if days<=7 else 'warning',
                                   'reason':'next_year_not_covered','daysRemaining':days,
                                   'thresholdDays':7 if days<=7 else 30 if days<=30 else 90})
    return result


def rollback(root, target, expected, reason, confirmation, cutoff=None):
    plan = rollback_plan(root, target, expected, reason, cutoff)
    require(confirmation == plan['confirmationSha256'], 'ops_rollback_confirmation_mismatch')
    # No release bytes are changed; an explicit operation record accompanies the pointer.
    _, active = snapshot(root)
    require(active['revision'] == expected, 'ops_rollback_current_revision_changed')
    health_path = root/'calendars/health.json'
    health = json.loads(health_path.read_bytes())
    require(health.get('schemaVersion') == 1 and health.get('activeRevision') == expected, 'ops_health_revision_mismatch')
    require(isinstance(json.loads((root/'calendars/sources.json').read_bytes()).get('calendars'),dict), 'ops_sources_invalid')
    history_path=root/'calendars/operations/history.json'
    if history_path.exists():
        history=json.loads(history_path.read_bytes())
        require(history.get('schemaVersion')==1 and isinstance(history.get('events'),list),'ops_history_invalid')
    _, previous = snapshot(root,target)
    index = {'schemaVersion':1, 'sha256':target, 'path':'calendars/releases/'+target+'.json'}
    health['activeRevision'] = target; health['lastRollback'] = plan
    health['coverageWarnings']=coverage(previous,cutoff or today())
    install_json(index, root/'calendars/current.json')
    install_json(health, health_path)
    # Keep a durable audit even when this CLI is invoked outside GitHub Actions.
    event_path = root/'calendars/operations/rollbacks'/ (plan['confirmationSha256']+'.json')
    install_json(plan, event_path)
    record(root, 'rollback', target, {'fromRevision':expected, 'confirmationSha256':confirmation})
    status(root)
    return plan


def status(root=ROOT, now=None, observe=False):
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    require(now.tzinfo is not None, 'ops_observation_time_ambiguous')
    _, active = snapshot(root)
    health = json.loads((root/'calendars/health.json').read_bytes())
    require(health.get('activeRevision') == active['revision'], 'ops_health_revision_mismatch')
    sources = json.loads((root/'calendars/sources.json').read_bytes())['calendars']
    rows = []
    for key, cal in sorted(active['data']['calendars'].items()):
        item = health.get('calendars', {}).get(key, {}); spec = sources.get(key, {})
        rows.append({'calendarId':key, 'adapter':spec.get('adapter'),
                     'capability':spec.get('capability', 'annual' if spec.get('adapter') != 'source-monitor' else 'monitor'),
                     'years':sorted(cal['years']), 'sourceStatus':item.get('status', 'not_checked'),
                     'lastAttemptAt':item.get('lastAttemptAt'), 'lastVerifiedAt':item.get('lastVerifiedAt'),
                     'reason':item.get('reason') or spec.get('blockReason'),
                     'unknownSessions':sum(len(r['unknownDates']) for r in cal['years'].values())})
    history = record(root, 'status', active['revision'], now=now) if observe else (
        json.loads((root/'calendars/operations/history.json').read_bytes())
        if (root/'calendars/operations/history.json').exists() else {'events':[]})
    dates = set()
    for event in history['events']:
        if event.get('kind') not in ('status', 'check'):continue
        observed = datetime.fromisoformat(event['at'])
        if observed.tzinfo is not None and observed <= now:
            dates.add(observed.astimezone(now.tzinfo).date())
    cursor = now.date(); consecutive = 0
    while cursor in dates:consecutive += 1; cursor -= timedelta(days=1)
    result = {'schemaVersion':1, 'activeRevision':active['revision'], 'observedAt':now.isoformat(timespec='seconds'),
              'calendars':rows, 'coverageWarnings':coverage(active,now.date()),
              'observation':{'distinctDays':len(dates), 'consecutiveDays':consecutive,
                             'thirtyDayObservationComplete':consecutive >= 30}, 'ledgerWrites':0}
    install_json(result, root/'calendars/status.json')
    lines = ['# 交易日历运行状态', '', '检查时间：'+result['observedAt'], '',
             '当前版本：`'+active['revision']+'`', '',
             '| 市场/产品 | 适配能力 | 已知年份 | 最近来源状态 | 待处理原因 |', '|---|---|---|---|---|']
    for row in rows:
        cells = [row['calendarId'], row['capability'], ', '.join(row['years']), row['sourceStatus'], row.get('reason') or '—']
        lines.append('| '+' | '.join(str(c).replace('|', '\\|').replace('\n', ' ') for c in cells)+' |')
    lines += ['', '连续观察：'+str(consecutive)+'天；达到30天：'+('是' if consecutive >= 30 else '否')+'。',
              '', '来源异常时继续使用覆盖目标日期的已核验日历。未知时段不推算。回滚及历史影响分析不自动改写账本。', '']
    (root/'calendars/status.md').write_text('\n'.join(lines), encoding='utf-8')
    return result


def request_operation(value, root=ROOT):
    require(isinstance(value, dict) and set(value) <= {'operation','target','expectedCurrent','reason','confirmation'},
            'ops_request_fields_invalid')
    require(all(isinstance(v,str) for v in value.values()), 'ops_request_value_invalid')
    operation = value.get('operation')
    require(operation in ('status','impact','rollback-plan','rollback'), 'ops_request_operation_invalid')
    if operation == 'status':return status(root)
    if operation == 'impact':
        _, before = snapshot(root);_, after = snapshot(root,value.get('target',''),candidate=True)
        return impact(before,after)
    target=value.get('target','');expected=value.get('expectedCurrent','');reason=value.get('reason','')
    if operation == 'rollback-plan':return rollback_plan(root,target,expected,reason)
    return rollback(root,target,expected,reason,value.get('confirmation',''))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('status', 'impact', 'rollback-plan', 'rollback', 'request'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--target', default=''); parser.add_argument('--expected-current', default='')
    parser.add_argument('--reason', default=''); parser.add_argument('--confirmation', default='')
    parser.add_argument('--ledger-dir'); parser.add_argument('--observe', action='store_true')
    args = parser.parse_args()
    if args.operation == 'request':result = request_operation(json.loads(os.environ.get('CALENDAR_OPS_REQUEST','')),args.root)
    elif args.operation == 'status':result = status(args.root, observe=args.observe)
    elif args.operation == 'impact':
        _, old = snapshot(args.root); _, new = snapshot(args.root, args.target, candidate=True)
        result = impact(old, new, ledger_dates=ledger_dates(args.ledger_dir) if args.ledger_dir else None)
    elif args.operation == 'rollback-plan':
        result = rollback_plan(args.root, args.target, args.expected_current, args.reason)
    else:
        require(args.ledger_dir is None, 'ops_rollback_never_accepts_ledger_mutation')
        result = rollback(args.root, args.target, args.expected_current, args.reason, args.confirmation)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':main()
