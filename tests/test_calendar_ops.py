from copy import deepcopy
from datetime import date,datetime,timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import calendar_ops as ops
import calendar_runtime as runtime


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.raw=runtime.seed_path().read_bytes();self.before=runtime.validate(self.raw)
        payload=deepcopy(self.before['data'])
        payload['calendars']['US_EQ']['years']['2027']['holidays']['2027-10-25']='测试专用休市'
        self.new_raw=runtime.canonical(payload);self.after=runtime.validate(self.new_raw)
        (self.root/'calendars/releases').mkdir(parents=True)
        for raw,s in ((self.raw,self.before),(self.new_raw,self.after)):
            (self.root/'calendars/releases'/(s['revision']+'.json')).write_bytes(raw)
        ops.install_json({'schemaVersion':1,'sha256':self.after['revision'],
                          'path':'calendars/releases/'+self.after['revision']+'.json'},self.root/'calendars/current.json')
        ops.install_json({'schemaVersion':1,'activeRevision':self.after['revision'],'calendars':{}},self.root/'calendars/health.json')
        ops.install_json({'calendars':{}},self.root/'calendars/sources.json')

    def test_impact_ignores_renames_but_finds_actual_historical_session_changes(self):
        report=ops.impact(self.before,self.after,date(2027,10,26),{'2027-10-25'})
        self.assertEqual(1,report['changedDays']);self.assertEqual(1,report['historicalChangedDays'])
        self.assertEqual(1,report['affectedLedgerCalendarDates']);self.assertEqual(0,report['ledgerWrites'])
        renamed=deepcopy(self.after);renamed['data']['calendars']['US_EQ']['years']['2027']['holidays']['2027-10-25']='另一名称'
        self.assertEqual(0,ops.impact(self.after,renamed)['changedDays'])

    def test_private_ledger_dates_are_read_only_and_records_never_enter_report(self):
        folder=self.root/'private-ledger';folder.mkdir()
        raw=runtime.canonical({'secretSymbol':'DO_NOT_PUBLISH','amount':12345,'days':{'2027-10-25':{'as_of':'2027-10-25'}}})
        path=folder/'ledger.json';path.write_bytes(raw)
        dates=ops.ledger_dates(folder);report=ops.impact(self.before,self.after,date(2027,10,26),dates)
        self.assertEqual({'2027-10-25'},dates);self.assertEqual(raw,path.read_bytes())
        self.assertNotIn('DO_NOT_PUBLISH',json.dumps(report));self.assertNotIn('12345',json.dumps(report))

    def test_preview_is_read_only_and_confirmation_binds_exact_versions_reason_and_day(self):
        before=(self.root/'calendars/current.json').read_bytes()
        plan=ops.rollback_plan(self.root,self.before['revision'],self.after['revision'],'恢复已验证的旧日历版本',date(2026,10,23))
        self.assertEqual(before,(self.root/'calendars/current.json').read_bytes())
        for reason,day in [('另一条足够长的回滚原因',date(2026,10,23)),('恢复已验证的旧日历版本',date(2026,10,24))]:
            other=ops.rollback_plan(self.root,self.before['revision'],self.after['revision'],reason,day)
            self.assertNotEqual(plan['confirmationSha256'],other['confirmationSha256'])

    def test_rollback_wrong_lease_or_confirmation_never_changes_public_pointer(self):
        raw=(self.root/'calendars/current.json').read_bytes()
        with self.assertRaises(ValueError):ops.rollback(self.root,self.before['revision'],self.after['revision'],'恢复已验证的旧日历版本','0'*64)
        with self.assertRaises(ValueError):ops.rollback_plan(self.root,self.before['revision'],'0'*64,'恢复已验证的旧日历版本')
        self.assertEqual(raw,(self.root/'calendars/current.json').read_bytes())

    def test_confirmed_rollback_preserves_release_bytes_and_audits_without_ledger_writes(self):
        reason='恢复已验证的旧日历版本';cutoff=date(2026,10,23)
        plan=ops.rollback_plan(self.root,self.before['revision'],self.after['revision'],reason,cutoff)
        applied=ops.rollback(self.root,self.before['revision'],self.after['revision'],reason,plan['confirmationSha256'],cutoff)
        self.assertEqual(self.before['revision'],ops.snapshot(self.root)[1]['revision'])
        self.assertEqual(self.raw,(self.root/'calendars/releases'/(self.before['revision']+'.json')).read_bytes())
        self.assertEqual(self.new_raw,(self.root/'calendars/releases'/(self.after['revision']+'.json')).read_bytes())
        self.assertEqual(0,applied['ledgerWrites'])
        self.assertTrue((self.root/'calendars/operations/rollbacks'/(plan['confirmationSha256']+'.json')).is_file())

    def test_candidates_can_be_compared_but_cannot_be_rollback_targets(self):
        path=self.root/'calendars/releases'/(self.before['revision']+'.json')
        candidate=self.root/'calendars/candidates'/path.name;candidate.parent.mkdir();candidate.write_bytes(path.read_bytes());path.unlink()
        self.assertEqual(self.before['revision'],ops.snapshot(self.root,self.before['revision'],candidate=True)[1]['revision'])
        with self.assertRaises(OSError):ops.rollback_plan(self.root,self.before['revision'],self.after['revision'],'恢复已验证的旧日历版本')

    def test_observation_needs_actual_dates_not_thirty_retries_on_one_day(self):
        now=datetime(2026,10,23,15,tzinfo=ZoneInfo('Asia/Shanghai'))
        for number in range(30):ops.record(self.root,'check',self.after['revision'],now=now,run_id=str(number+1))
        first=ops.status(self.root,now=now)
        self.assertEqual(1,first['observation']['consecutiveDays']);self.assertFalse(first['observation']['thirtyDayObservationComplete'])
        for days in range(1,30):ops.record(self.root,'check',self.after['revision'],now=now-timedelta(days=days),run_id=str(100+days))
        self.assertTrue(ops.status(self.root,now=now)['observation']['thirtyDayObservationComplete'])

    def test_future_observation_does_not_complete_todays_history(self):
        now=datetime(2026,10,23,15,tzinfo=ZoneInfo('Asia/Shanghai'))
        ops.record(self.root,'check',self.after['revision'],now=now+timedelta(days=1),run_id='1')
        self.assertEqual(0,ops.status(self.root,now=now)['observation']['consecutiveDays'])

    def test_public_operations_request_is_data_not_shell_or_arbitrary_file_access(self):
        for value in ({'operation':'shell','command':'echo secret'}, {'operation':'impact','target':'../../private'},
                      {'operation':'status','root':'/private'}, {'operation':'rollback','target':False}):
            with self.assertRaises(ValueError):ops.request_operation(value,self.root)


if __name__=='__main__':unittest.main()
