"""Failure recovery and unattended publication must retain provenance/history."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import calendar_automation as a
import calendar_sync as s
import calendar_runtime as r
import calendar_ops as ops

NOW=datetime(2026,10,23,20,tzinfo=ZoneInfo('Asia/Shanghai'))
FIX=Path(__file__).with_name('fixtures')


def root_snapshot(root):
    raw=r.seed_path().read_bytes();revision=r.validate(raw)['revision']
    path='calendars/releases/'+revision+'.json';(root/'calendars/releases').mkdir(parents=True)
    (root/path).write_bytes(raw)
    (root/'calendars/current.json').write_bytes(r.canonical({'schemaVersion':1,'sha256':revision,'path':path}))
    return r.validate(raw)


class AutomaticCalendarTests(unittest.TestCase):
    def test_sgx_public_configuration_and_structured_download_items_are_identity_bound(self):
        cfg={'endpoints':{'CMS_API_URL':'https://api2.sgx.com/content-api'},'CMS_VERSION':'a'*40}
        url=a.sgx_cms_url(json.dumps(cfg))
        self.assertIn('queryId='+('a'*40)+'%3Apage',url)
        self.assertIn('fx-solutions',url)
        cfg['endpoints']['CMS_API_URL']='https://evil.example/api'
        with self.assertRaises(ValueError):a.sgx_cms_url(json.dumps(cfg))
        doc='https://api2.sgx.com/sites/default/files/2026-12/SGX%20Calendar%202027.pdf'
        value={'data':{'downloadItems':[{'data':{'label':'SGX Calendar 2027','file':{'data':{'file':{'data':{'url':doc}}}}}}]}}
        self.assertEqual([doc],a.discover_documents(json.dumps(value),'https://api2.sgx.com/content-api',s.SOURCES['SGX_NIKKEI']['documentPatterns'],label_pattern='SGX Calendar'))
        with self.assertRaises(ValueError):a.discover_documents('{"errors":[{"message":"changed"}]}','https://api2.sgx.com/content-api',[])

    def test_recovery_and_unverified_next_year_have_automatic_daily_checks(self):
        cal=r.validate(r.seed_path().read_bytes())['data']['calendars']['US_EQ']
        state={'lastAttemptAt':NOW.isoformat(),'parsedYears':['2026']}
        due,reason=s.should_check(cal,state,NOW+timedelta(days=1),adapter='us-pair')
        self.assertTrue(due);self.assertEqual('q4_missing_next_year',reason)
        before_q4=NOW.replace(month=7)
        state={'lastAttemptAt':before_q4.isoformat(),'recoverableSourceFailure':True}
        self.assertEqual((True,'automatic_source_recovery'),s.should_check(cal,state,before_q4+timedelta(days=1),adapter='us-pair'))
        self.assertFalse(s.should_check(cal,state,before_q4+timedelta(hours=1),adapter='us-pair')[0])
        self.assertFalse(s.should_check(cal,state,before_q4+timedelta(days=1),adapter='blocked-a50-provider')[0])

    def test_annual_chinese_table_spacing_does_not_lose_holiday_scope(self):
        text=json.loads((FIX/'shfe-2026.json').read_text())['text']
        expected=s.parse_china(text)
        table=text.replace('2026年休市安排','2026 年休市安排').replace('元旦：','元 旦 ').replace('春节：','春 节 ')
        self.assertEqual(expected,s.parse_china(table))

    def test_transient_transport_retries_are_bounded_and_http_denials_do_not_retry(self):
        calls=[];delays=[]
        def flaky(url):
            calls.append(url)
            if len(calls)<3:raise URLError('temporary')
            return b'official'
        self.assertEqual((b'official',3),a.fetch_retry(flaky,'https://example.com',pause=delays.append))
        self.assertEqual([.25,.5],delays)
        for code,count in ((503,3),(403,1),(412,1)):
            calls.clear()
            def denied(url):calls.append(url);raise HTTPError(url,code,'failure',{},None)
            with self.assertRaises(HTTPError):a.fetch_retry(denied,'https://example.com',pause=lambda _:None)
            self.assertEqual(count,len(calls))

    def test_discovery_uses_visible_links_exact_host_and_bounded_document_shape(self):
        pattern=r'https://api2\.sgx\.com/sites/default/files/20[0-9]{2}-[0-9]{2}/SGX%20Calendar%2020[0-9]{2}(?:_[0-9]+)?\.pdf'
        good='https://api2.sgx.com/sites/default/files/2026-12/SGX%20Calendar%202027.pdf'
        html='<a href="'+good+'">SGX <b>Calendar</b> 2027</a>'
        html+='<script><a href="'+good.replace('2027.pdf','2028.pdf')+'">Calendar</a></script>'
        html+='<a href="'+good.replace('api2.sgx.com','evil.example')+'">Calendar</a>'
        html+='<a href="'+good.replace('https://','http://')+'">Calendar</a>'
        self.assertEqual([good],a.discover_documents(html,'https://www.sgx.com/derivatives/trading',[pattern],label_pattern='calendar'))
        obj=json.dumps({'data':{'html':html}})
        self.assertEqual([good],a.discover_documents(obj,'https://www.sgx.com/derivatives/trading',[pattern],label_pattern='calendar'))
        with self.assertRaises(ValueError):a.discover_documents(html,'https://www.sgx.com',[pattern],label_pattern='calendar',limit=0)

    def test_future_projection_keeps_today_history_and_does_not_guess_removed_sessions(self):
        old=s.blank('2026',['https://official.example'])
        old['holidays']={'2026-02-01':'past','2026-10-23':'today','2026-12-25':'old'}
        old['overrides']={'2026-12-24':{'closeMinute':720}}
        old['unknownDates']=['2026-12-30']
        new=s.blank('2026',['https://official.example'])
        new['holidays']={'2026-01-15':'retroactive','2026-11-10':'future'}
        actual=a.future_record(old,new,NOW.date())
        self.assertEqual({'2026-02-01':'past','2026-10-23':'today','2026-11-10':'future'},actual['holidays'])
        self.assertEqual(['2026-12-24','2026-12-25','2026-12-30'],actual['unknownDates'])
        self.assertFalse(actual['overrides'])
        self.assertIn('2026-12-25',old['holidays'])

    def test_explicit_holiday_or_verified_clock_can_resolve_future_unknown(self):
        old=s.blank('2027',['https://official.example']);old['unknownDates']=['2027-01-01','2027-12-24']
        new=deepcopy(old);new['unknownDates']=[]
        new['holidays']={'2027-01-01':'New Year'};new['overrides']={'2027-12-24':{'closeMinute':720}}
        self.assertEqual([],a.future_record(old,new,NOW.date())['unknownDates'])

    def test_missing_current_year_cannot_retroactively_claim_known_history(self):
        new=s.blank('2026',['https://official.example']);new['holidays']={'2026-01-01':'New Year'}
        actual=a.future_record(None,new,NOW.date())
        self.assertIn('2026-10-23',actual['unknownDates']);self.assertNotIn('2026-10-24',actual['unknownDates'])
        self.assertFalse(actual['holidays'])

    def test_cache_retains_original_proof_and_rejects_expiry_wrong_scope_and_tampering(self):
        text=json.loads((FIX/'shfe-2026.json').read_text())['text']
        from calendar_adapters import parse_china_futures
        records,nights=parse_china_futures(text);url=s.SOURCES['CN_FUTURES']['urls'][0]
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);digest=a.save_venue_cache(root,'SHFE',url,'a'*64,records,nights,NOW)
            data,actual,proof=a.load_venue_cache(root,'SHFE',url,NOW+timedelta(days=1))
            self.assertEqual(records,data);self.assertEqual(nights,actual)
            self.assertEqual(NOW.isoformat(timespec='seconds'),proof['sourceCheckedAt'])
            for venue,when in (('INE',NOW),('SHFE',NOW+timedelta(days=15)),('SHFE',NOW-timedelta(seconds=1)),('SHFE',NOW.replace(year=2027))):
                with self.assertRaises(ValueError):a.load_venue_cache(root,venue,url,when)
            path=root/'calendars/source-cache'/(digest+'.json');path.write_bytes(path.read_bytes()+b' ')
            with self.assertRaises(ValueError):a.load_venue_cache(root,'SHFE',url,NOW)

    def test_all_five_live_parses_can_recover_from_outage_without_refreshing_source_time(self):
        spec=deepcopy(s.SOURCES['CN_FUTURES']);spec.pop('venueDiscovery',None);spec.pop('venueAlternatives',None)
        spec['urls'][3]='https://www.czce.com.cn/cn/annual-test.htm'
        text=json.loads((FIX/'shfe-2026.json').read_text())['text']
        with tempfile.TemporaryDirectory() as td, patch.dict(s.SOURCES,{'CN_FUTURES':spec},clear=True):
            root=Path(td);root_snapshot(root)
            s.sync(root,force=True,now=NOW,fetch=lambda u:text.encode())
            health=lambda:json.loads((root/'calendars/health.json').read_bytes())['calendars']['CN_FUTURES']
            stamp=health()['lastSourceSuccessAt']
            def offline(u):raise OSError('offline')
            s.sync(root,force=True,now=NOW+timedelta(days=1),fetch=offline)
            state=health();self.assertEqual('verified_with_validated_cache',state['status'])
            self.assertEqual(stamp,state['lastSourceSuccessAt']);self.assertEqual(5,len(state['cachedVenues']))
            self.assertTrue(all(p['status']=='unavailable' for p in state['sourceResults']))
            self.assertTrue(all(v['status']=='validated_source_cache' for v in state['venueValidation'].values()))
            s.sync(root,force=True,now=NOW+timedelta(days=15),fetch=offline)
            self.assertEqual('held_preserving_active_calendar',health()['status'])

    def test_fresh_changed_format_cannot_be_masked_by_old_parsed_cache(self):
        text=json.loads((FIX/'shfe-2026.json').read_text())['text']
        spec=deepcopy(s.SOURCES['CN_FUTURES']);spec.pop('venueDiscovery',None);spec.pop('venueAlternatives',None)
        spec['urls'][3]='https://www.czce.com.cn/cn/annual-test.htm'
        with tempfile.TemporaryDirectory() as td,patch.dict(s.SOURCES,{'CN_FUTURES':spec},clear=True):
            root=Path(td);root_snapshot(root);s.sync(root,force=True,now=NOW,fetch=lambda _:text.encode())
            s.sync(root,force=True,now=NOW+timedelta(days=1),fetch=lambda _:b'changed official format')
            state=json.loads((root/'calendars/health.json').read_bytes())['calendars']['CN_FUTURES']
            self.assertEqual('held_preserving_active_calendar',state['status'])
            self.assertTrue(all(v['status']=='held' for v in state['venueValidation'].values()))

    def test_unattended_future_publication_persists_impact_and_never_changes_history(self):
        spec=deepcopy(s.SOURCES['BR_EQ'])
        from test_calendar_extended_adapters import b3
        with tempfile.TemporaryDirectory() as td,patch.dict(s.SOURCES,{'BR_EQ':spec},clear=True):
            root=Path(td);before=root_snapshot(root)
            # A new future holiday plus a retroactive addition in a complete
            # source must only update the future. Existing parser is unchanged.
            from calendar_adapters import parse_b3
            proposed=parse_b3(b3())
            proposed['2026']['holidays']['2026-01-15']='Historical correction'
            proposed['2026']['holidays']['2026-11-02']='Future closure'
            proposed['2026']['holidays']['2026-10-26']='New future closure'
            with patch('calendar_adapters.parse_b3',return_value=proposed):
                result=s.sync(root,force=True,now=NOW,fetch=lambda _:b3().encode())
            self.assertIn('BR_EQ:2026',result['changedCalendars'])
            _,after=ops.snapshot(root);assessment=ops.impact(before,after,NOW.date())
            self.assertEqual(0,assessment['historicalChangedDays'])
            self.assertEqual('CLOSED',r.day_rule(after,'BR_EQ','2026-10-26')['kind'])
            self.assertEqual(r.day_rule(before,'BR_EQ','2026-01-15')['kind'],r.day_rule(after,'BR_EQ','2026-01-15')['kind'])
            audit=list((root/'calendars/operations/automatic').glob('*.json'));self.assertEqual(1,len(audit))
            entry=json.loads(audit[0].read_bytes());self.assertEqual(0,entry['ledgerWrites'])
            self.assertEqual(0,entry['impact']['historicalChangedDays'])
            self.assertTrue(list((root/'calendars/candidates').glob('*.impact.json')))


if __name__=='__main__':unittest.main()
