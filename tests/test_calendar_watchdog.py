from copy import deepcopy
from datetime import datetime, timedelta
import base64
import hashlib
import json
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import calendar_runtime as runtime
import calendar_watchdog as watch


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 23, 8, 47, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.raw = runtime.seed_path().read_bytes()
        self.snapshot = runtime.validate(self.raw)
        self.health = {'schemaVersion': 1, 'activeRevision': self.snapshot['revision'],
                       'calendars': {k: {'lastAttemptAt': self.now.isoformat(), 'adapter': 'test',
                                        'status': 'verified_no_change'} for k in self.snapshot['data']['calendars']}}
        self.runs = [self.sample_run(1, 'completed', 'success', hours=1, event='push')]

    def sample_run(self, rid, status='completed', conclusion='success', *, hours=1, event='workflow_dispatch'):
        return {'id': rid, 'status': status, 'conclusion': conclusion, 'head_branch': 'main',
                'path': '.github/workflows/calendar-sync.yml', 'event': event,
                'created_at': (self.now - timedelta(hours=hours)).isoformat(),
                'updated_at': (self.now - timedelta(hours=hours)).isoformat()}

    def assess(self):
        return watch.assess(self.snapshot, self.health, self.runs, self.now)

    def test_current_checks_keep_missing_year_attention_without_unnecessary_recovery(self):
        report = self.assess()
        self.assertEqual(report['recoveryDecision'], 'within_cadence')
        cn = next(x for x in report['calendars'] if x['calendarId'] == 'CN_EQ')
        self.assertEqual(cn['coverageDaysRemaining'], 69)
        self.assertEqual(cn['limitHours'], 48)
        self.assertEqual(report['status'], 'attention')

    def test_q4_missing_year_overdue_but_covered_calendar_has_eight_days(self):
        for key in ('CN_EQ', 'US_EQ'):
            self.health['calendars'][key]['lastAttemptAt'] = (self.now - timedelta(hours=49)).isoformat()
        self.assertEqual(self.assess()['overdueCalendars'], ['CN_EQ'])
        self.assertEqual(self.assess()['recoveryDecision'], 'recovery_due')
        self.health['calendars']['US_EQ']['lastAttemptAt'] = (self.now - timedelta(hours=193)).isoformat()
        self.assertIn('US_EQ', self.assess()['overdueCalendars'])

    def test_before_q4_all_calendars_use_weekly_threshold(self):
        self.now = self.now.replace(month=9)
        self.set_health_time(self.now - timedelta(days=3))
        self.runs = [self.sample_run(1, hours=72, event='schedule')]
        self.assertFalse(self.assess()['overdueCalendars'])
        self.assertEqual(self.assess()['recoveryDecision'], 'within_cadence')

    def test_unverified_next_official_year_is_daily_even_when_seed_has_coverage(self):
        self.health['calendars']['US_EQ'].update(parsedYears=['2026'],lastAttemptAt=(self.now-timedelta(hours=49)).isoformat())
        self.assertIn('US_EQ',self.assess()['overdueCalendars'])

    def test_transport_recovery_is_daily_before_q4_but_blocked_provider_stays_weekly(self):
        self.now=self.now.replace(month=7)
        self.set_health_time(self.now)
        self.health['calendars']['US_EQ'].update(recoverableSourceFailure=True,lastAttemptAt=(self.now-timedelta(hours=49)).isoformat())
        self.health['calendars']['A50_CFD'].update(adapter='blocked-a50-provider',recoverableSourceFailure=True,
            lastAttemptAt=(self.now-timedelta(hours=49)).isoformat())
        self.assertIn('US_EQ',self.assess()['overdueCalendars'])
        self.assertNotIn('A50_CFD',self.assess()['overdueCalendars'])

    def set_health_time(self, instant):
        for rec in self.health['calendars'].values():
            rec['lastAttemptAt'] = instant.isoformat()

    def test_rolling_cfd_reference_checks_daily_before_q4(self):
        self.now=self.now.replace(month=7)
        self.set_health_time(self.now)
        self.health['calendars']['A50_CFD'].update(adapter='a50-cfd-reference',
            lastAttemptAt=(self.now-timedelta(hours=49)).isoformat())
        self.assertIn('A50_CFD',self.assess()['overdueCalendars'])

    def test_rolling_reference_coverage_uses_window_instead_of_unknown_baseline_years(self):
        cal=self.snapshot['data']['calendars']['A50_CFD']
        cal['years']['2026']['reference']={'evidenceLevel':'third_party_reference','end':'2026-11-27'}
        self.health['calendars']['A50_CFD']['adapter']='a50-cfd-reference'
        item=next(r for r in self.assess()['calendars'] if r['calendarId']=='A50_CFD')
        self.assertEqual(35,item['coverageDaysRemaining']);self.assertEqual(48,item['limitHours'])

    def test_missing_malformed_future_or_naive_timestamp_is_not_fresh(self):
        for value in (None, 'bad', '2026-10-23T08:47:00', (self.now + timedelta(hours=1)).isoformat()):
            self.health['calendars']['CN_EQ']['lastAttemptAt'] = value
            self.assertIn('CN_EQ', self.assess()['overdueCalendars'])

    def test_active_stuck_run_is_reported_and_never_duplicated(self):
        self.set_health_time(self.now - timedelta(days=10))
        self.runs.append(self.sample_run(2, 'queued', None, hours=2))
        report = self.assess()
        self.assertEqual(report['recoveryDecision'], 'active_run_no_duplicate')
        self.assertEqual(report['stuckRunIds'], [2])

    def test_recent_failed_dispatch_blocks_retry_for_24_hours(self):
        self.set_health_time(self.now - timedelta(days=10))
        self.runs.append(self.sample_run(2, conclusion='failure', hours=23))
        self.assertEqual(self.assess()['recoveryDecision'], 'dispatch_cooldown')
        self.runs[-1] = self.sample_run(2, conclusion='failure', hours=25)
        self.assertEqual(self.assess()['recoveryDecision'], 'recovery_due')

    def test_other_branch_or_workflow_success_does_not_hide_missing_execution(self):
        self.runs[0]['head_branch'] = 'other'
        self.assertTrue(self.assess()['workflowOverdue'])
        self.runs[0]['head_branch'] = 'main'
        self.runs[0]['path'] = '.github/workflows/other.yml'
        self.assertTrue(self.assess()['workflowOverdue'])

    def test_source_failure_is_distinct_from_overdue(self):
        self.health['calendars']['US_EQ']['status'] = 'held_preserving_active_calendar'
        report = self.assess()
        self.assertEqual(report['status'], 'attention')
        self.assertEqual(report['recoveryDecision'], 'within_cadence')

    def test_monitor_only_venue_does_not_cause_daily_network_recovery(self):
        self.health['calendars']['CN_FUTURES'].update(adapter='source-monitor',lastAttemptAt=(self.now-timedelta(days=3)).isoformat())
        report=self.assess()
        self.assertNotIn('CN_FUTURES',report['overdueCalendars'])
        self.assertEqual(next(x for x in report['calendars'] if x['calendarId']=='CN_FUTURES')['limitHours'],192)

    def test_scope_blocked_or_unvalidated_adapters_use_weekly_threshold(self):
        for adapter,capability in [('blocked-a50-provider','blocked'),('bse-equities-annual','awaiting_live_validation'),
                                   ('china-futures-consensus','awaiting_source_consensus')]:
            self.health['calendars']['CN_FUTURES'].update(adapter=adapter,capability=capability,
                lastAttemptAt=(self.now-timedelta(days=3)).isoformat())
            self.assertNotIn('CN_FUTURES',self.assess()['overdueCalendars'])

    def test_recovery_has_fixed_allowlist_and_no_force_or_strategy_dispatch(self):
        report = {**self.assess(), 'recoveryDecision': 'recovery_due'}
        env = {'GITHUB_REPOSITORY': watch.REPO, 'GITHUB_REF': 'refs/heads/main', 'GH_TOKEN': 'test-only'}
        calls = []
        def send(path, **kwargs):
            calls.append((path, kwargs)); return {'workflow_run_id': 9}
        result = watch.recover(report, env, send)
        self.assertEqual(result['recoveryRunId'], 9)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], '/repos/chaonpu/ziwuxilv/actions/workflows/calendar-sync.yml/dispatches')
        self.assertEqual(calls[0][1]['body']['inputs'], {'force': False})
        for field, bad in [('GITHUB_REPOSITORY', 'chaonpu/Runner'), ('GITHUB_REF', 'refs/heads/other')]:
            with self.assertRaises(ValueError):
                watch.recover(report, {**env, field: bad}, send)
        self.assertEqual(len(calls), 1)

    def test_ambiguous_dispatch_never_retries(self):
        calls = []
        def send(*args, **kwargs):
            calls.append(1); raise OSError('ambiguous transport failure')
        result = watch.recover({**self.assess(), 'recoveryDecision': 'recovery_due'},
                               {'GITHUB_REPOSITORY': watch.REPO, 'GITHUB_REF': 'refs/heads/main', 'GH_TOKEN': 'test'}, send)
        self.assertEqual(result['recoveryDecision'], 'dispatch_response_uncertain_no_retry')
        self.assertEqual(calls, [1])

    def test_snapshot_and_health_are_read_at_same_commit_and_blob_checked(self):
        commit = 'a' * 40
        revision = self.snapshot['revision']
        files = {'calendars/current.json': runtime.canonical({'schemaVersion': 1, 'sha256': revision,
                 'path': 'calendars/releases/' + revision + '.json'}),
                 'calendars/releases/' + revision + '.json': self.raw,
                 'calendars/health.json': runtime.canonical(self.health)}
        refs = []
        def read(path):
            if path.endswith('/commits/main'):return {'sha': commit}
            if '/contents/' in path:
                file, ref = path.split('/contents/')[1].split('?ref=')
                refs.append(ref); raw = files[file]
                return {'type': 'file', 'path': file, 'encoding': 'base64',
                        'content': base64.b64encode(raw).decode(),
                        'sha': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}
            return {'workflow_runs': self.runs}
        self.assertEqual(watch.inspect_remote(read, self.now)['calendarCommit'], commit)
        self.assertEqual(refs, [commit] * 3)
        self.health['activeRevision'] = '0' * 64
        files['calendars/health.json'] = runtime.canonical(self.health)
        with self.assertRaises(ValueError):watch.inspect_remote(read, self.now)

    def test_drill_does_not_override_active_run_or_cooldown(self):
        env = {'GITHUB_REPOSITORY': watch.REPO, 'GITHUB_REF': 'refs/heads/main', 'GH_TOKEN': 'test'}
        for decision in ('active_run_no_duplicate', 'dispatch_cooldown'):
            report = {**self.assess(), 'recoveryDecision': decision}
            self.assertEqual(watch.recover(report, env, lambda *a, **k: self.fail('unwanted dispatch'), drill=True), report)


if __name__ == '__main__':unittest.main()
