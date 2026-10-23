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

    def prepare_review(self):
        candidate=self.before['revision'];year='2027';key='US_EQ'
        path=self.root/'calendars/candidates'/(candidate+'.json');path.parent.mkdir();path.write_bytes(self.raw)
        sources=self.before['data']['calendars'][key]['years'][year]['sources']
        health={'schemaVersion':1,'activeRevision':self.after['revision'],'calendars':{key:{
            'candidateRevisions':{year:candidate},'heldYears':{year:'candidate_removes_holiday'},
            'sourceResults':[{'url':url,'status':'ok','sha256':'a'*64} for url in sources]}}}
        ops.install_json(health,self.root/'calendars/health.json')
        ops.install_json({'calendars':{key:{'adapter':'us-pair','capability':'annual','urls':sources}}},self.root/'calendars/sources.json')
        return candidate,self.after['revision'],key,year,'核对官方公告后移除测试专用未来日期'

    def test_review_plan_is_read_only_and_confirmation_binds_proof_and_scope(self):
        args=self.prepare_review();pointer=(self.root/'calendars/current.json').read_bytes()
        plan,raw=ops.reviewed_plan(self.root,*args,date(2026,10,23))
        self.assertEqual(pointer,(self.root/'calendars/current.json').read_bytes())
        self.assertEqual(1,plan['changedDays']);self.assertEqual(0,plan['historicalChangedDays'])
        health=json.loads((self.root/'calendars/health.json').read_bytes())
        health['calendars']['US_EQ']['sourceResults'][0]['sha256']='b'*64
        ops.install_json(health,self.root/'calendars/health.json')
        other,_=ops.reviewed_plan(self.root,*args,date(2026,10,23))
        self.assertNotEqual(plan['confirmationSha256'],other['confirmationSha256'])

    def test_reviewed_publication_updates_only_reviewed_year_and_keeps_release_bytes(self):
        args=self.prepare_review();plan,_=ops.reviewed_plan(self.root,*args,date(2026,10,23))
        result=ops.publish_reviewed(self.root,*args,plan['confirmationSha256'],date(2026,10,23))
        self.assertEqual(self.before['revision'],ops.snapshot(self.root)[1]['revision'])
        self.assertEqual(0,result['ledgerWrites'])
        self.assertEqual(self.new_raw,(self.root/'calendars/releases'/(self.after['revision']+'.json')).read_bytes())
        self.assertTrue((self.root/'calendars/operations/reviews'/(plan['confirmationSha256']+'.json')).exists())

    def test_review_refuses_historical_wrong_confirmation_or_unvalidated_source_without_pointer_change(self):
        args=self.prepare_review();pointer=(self.root/'calendars/current.json').read_bytes()
        with self.assertRaises(ValueError):ops.reviewed_plan(self.root,*args,date(2028,1,1))
        with self.assertRaises(ValueError):ops.publish_reviewed(self.root,*args,'0'*64,date(2026,10,23))
        ops.install_json({'calendars':{'US_EQ':{'capability':'blocked'}}},self.root/'calendars/sources.json')
        with self.assertRaises(ValueError):ops.reviewed_plan(self.root,*args,date(2026,10,23))
        self.assertEqual(pointer,(self.root/'calendars/current.json').read_bytes())

    def test_review_requires_latest_official_candidate_and_successful_source_proof(self):
        args=self.prepare_review();health=json.loads((self.root/'calendars/health.json').read_bytes())
        health['calendars']['US_EQ']['sourceResults'][0]['status']='unavailable'
        ops.install_json(health,self.root/'calendars/health.json')
        with self.assertRaises(ValueError):ops.reviewed_plan(self.root,*args,date(2026,10,23))
        health['calendars']['US_EQ']['sourceResults'][0]['status']='ok'
        health['calendars']['US_EQ']['candidateRevisions']['2027']='0'*64
        ops.install_json(health,self.root/'calendars/health.json')
        with self.assertRaises(ValueError):ops.reviewed_plan(self.root,*args,date(2026,10,23))

    def test_old_candidate_cannot_revert_other_markets_in_current_version(self):
        args=self.prepare_review();data=deepcopy(self.after['data'])
        data['calendars']['CN_EQ']['session']['openMinute']=571
        raw=runtime.canonical(data);active=runtime.validate(raw)
        (self.root/'calendars/releases'/(active['revision']+'.json')).write_bytes(raw)
        ops.install_json({'schemaVersion':1,'sha256':active['revision'],'path':'calendars/releases/'+active['revision']+'.json'},self.root/'calendars/current.json')
        health=json.loads((self.root/'calendars/health.json').read_bytes());health['activeRevision']=active['revision']
        ops.install_json(health,self.root/'calendars/health.json')
        args=(args[0],active['revision'],*args[2:])
        plan,proposed=ops.reviewed_plan(self.root,*args,date(2026,10,23))
        self.assertEqual(571,runtime.validate(proposed)['data']['calendars']['CN_EQ']['session']['openMinute'])
        self.assertEqual(1,plan['changedDays'])


if __name__=='__main__':unittest.main()
