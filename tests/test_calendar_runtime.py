from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib,json,sys,tempfile,unittest
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import calendar_runtime as runtime
import calendar_sync as sync


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.raw=runtime.seed_path().read_bytes();self.snapshot=runtime.validate(self.raw)

    def test_digest_and_all_existing_market_coverage(self):
        self.assertEqual(len(self.snapshot['data']['calendars']),17)
        self.assertEqual(self.snapshot['revision'],hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(runtime.day_rule(self.snapshot,'CN_EQ','2027-01-04')['kind'],'UNKNOWN')
        self.assertEqual(runtime.day_rule(self.snapshot,'US_EQ','2027-01-04')['kind'],'NORMAL')
        self.assertEqual(runtime.day_rule(self.snapshot,'US_EQ','2027-11-26')['closeMinute'],780)

    def test_no_published_equity_year_silently_becomes_an_empty_calendar(self):
        for key,calendar in self.snapshot['data']['calendars'].items():
            if not key.endswith('_EQ'):continue
            for year,record in calendar['years'].items():
                with self.subTest(calendar=key,year=year):
                    self.assertGreaterEqual(len(record['holidays']),5)

    def test_cross_year_and_unknown_adjacent_never_guess(self):
        self.assertEqual(runtime.adjacent(self.snapshot,'US_EQ',date(2027,1,4),-1),date(2026,12,31))
        self.assertIsNone(runtime.adjacent(self.snapshot,'CN_EQ',date(2026,12,31),1))
        self.assertEqual(runtime.adjacent(self.snapshot,'US_EQ',date(2026,11,25),1),date(2026,11,27))

    def test_weekend_makeup_is_not_stock_trading(self):
        self.assertEqual(runtime.day_rule(self.snapshot,'CN_EQ','2026-10-10')['kind'],'CLOSED')
        self.assertEqual(runtime.day_rule(self.snapshot,'CN_EQ','2026-02-23')['kind'],'CLOSED')
        self.assertEqual(runtime.day_rule(self.snapshot,'CN_EQ','2026-02-24')['kind'],'NORMAL')

    def test_product_specific_unknown_does_not_close_other_venues(self):
        self.assertEqual(runtime.day_rule(self.snapshot,'CME_FUTURES','2026-11-27')['kind'],'UNKNOWN')
        self.assertEqual(runtime.day_rule(self.snapshot,'US_EQ','2026-11-27')['kind'],'NORMAL')
        self.assertEqual(runtime.day_rule(self.snapshot,'HK_EQ','2026-12-24')['halfDay'],True)

    def test_structural_tampering_rejected(self):
        for mutation in ('timezone','coverage','date','overlap','clock'):
            data=deepcopy(self.snapshot['data']);cal=data['calendars']['CN_EQ'];r=cal['years']['2026']
            if mutation=='timezone':cal['timezone']='bad-zone'
            elif mutation=='coverage':r['coverageEnd']='2027-12-31'
            elif mutation=='date':r['holidays']['2027-01-01']='bad'
            elif mutation=='overlap':r['unknownDates'].append('2026-01-01')
            else:cal['session']['closeMinute']=9999
            with self.subTest(mutation=mutation),self.assertRaises((ValueError,KeyError)):
                runtime.validate(runtime.canonical(data))

    def test_remote_identity_and_digest_rejected(self):
        index={'schemaVersion':1,'sha256':self.snapshot['revision'],'path':'calendars/releases/'+self.snapshot['revision']+'.json'}
        def get(path):return json.dumps(index).encode() if path.endswith('current.json') else self.raw
        self.assertEqual(runtime.remote(fetch=get)[1]['revision'],self.snapshot['revision'])
        index['path']='https://untrusted.invalid/calendar.json'
        with self.assertRaises(ValueError):runtime.remote(fetch=get)
        with self.assertRaises(ValueError):runtime.remote('0'*64,fetch=lambda p:self.raw)

    def test_offline_cache_is_usable_but_wrong_pinned_version_is_not(self):
        import os
        from unittest.mock import patch
        def offline(path):raise OSError('offline')
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{},clear=True):
            target=Path(td)/'snapshot.json'
            result=runtime.pin_latest(target,revision=self.snapshot['revision'],fetch=offline)
            self.assertEqual(result['calendarOrigin'],'bundled')
            self.assertEqual(target.read_bytes(),self.raw)
            with self.assertRaises(ValueError):runtime.pin_latest(target,revision='0'*64,fetch=offline)
        runtime._snapshot=None

    def test_verified_version_survives_job_temp_cleanup(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{'RUNNER_TOOL_CACHE':str(Path(td)/'tools')},clear=True):
            data=deepcopy(self.snapshot['data']);data['baselineReviewedAt']='persistence-fixture'
            raw=runtime.canonical(data);revision=hashlib.sha256(raw).hexdigest()
            first=Path(td)/'first-job.json'
            runtime.pin_latest(first,revision=revision,fetch=lambda path:raw)
            first.unlink()
            def offline(path):raise OSError('offline')
            second=Path(td)/'second-job.json'
            result=runtime.pin_latest(second,revision=revision,fetch=offline)
            self.assertEqual(result['calendarOrigin'],'verified_cache')
            self.assertEqual(second.read_bytes(),raw)
        runtime._snapshot=None

    def test_daily_only_q4_and_missing_next_year(self):
        now=datetime(2026,10,23,8,17,tzinfo=ZoneInfo('Asia/Shanghai'))
        state={'lastAttemptAt':(now-timedelta(days=1,minutes=1)).isoformat()}
        cn=self.snapshot['data']['calendars']['CN_EQ'];us=self.snapshot['data']['calendars']['US_EQ']
        self.assertEqual(sync.should_check(cn,state,now),(True,'q4_missing_next_year'))
        self.assertEqual(sync.should_check(us,state,now),(False,'weekly'))
        self.assertFalse(sync.should_check(cn,state,now.replace(month=9))[0])
        self.assertTrue(sync.should_check(us,state,now,force=True)[0])

    def test_weekly_and_daily_checks_tolerate_scheduler_jitter(self):
        now=datetime(2026,10,25,8,17,tzinfo=ZoneInfo('Asia/Shanghai'))
        us=self.snapshot['data']['calendars']['US_EQ'];cn=self.snapshot['data']['calendars']['CN_EQ']
        self.assertTrue(sync.should_check(us,{'lastAttemptAt':(now-timedelta(days=7)+timedelta(minutes=9)).isoformat()},now)[0])
        self.assertTrue(sync.should_check(cn,{'lastAttemptAt':(now-timedelta(days=1)+timedelta(minutes=9)).isoformat()},now)[0])

    def test_failed_sources_preserve_active_bytes_and_check_skip_does_no_network(self):
        import shutil
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'calendars/releases').mkdir(parents=True)
            index={'schemaVersion':1,'sha256':self.snapshot['revision'],'path':'calendars/releases/'+self.snapshot['revision']+'.json'}
            (root/'calendars/current.json').write_bytes(runtime.canonical(index))
            (root/index['path']).write_bytes(self.raw)
            now=datetime(2026,10,23,8,17,tzinfo=ZoneInfo('Asia/Shanghai'))
            def fail(url):raise OSError('offline')
            result=sync.sync(root,now=now,fetch=fail)
            self.assertEqual(result['activeRevision'],self.snapshot['revision'])
            self.assertFalse(result['changedCalendars'])
            calls=[]
            result=sync.sync(root,now=now+timedelta(hours=1),fetch=lambda u:calls.append(u))
            self.assertFalse(calls);self.assertEqual(result['sourceChecks'],0)
            hp=root/'calendars/health.json';health=json.loads(hp.read_bytes())
            stamp=(now-timedelta(days=1)).isoformat()
            health['calendars']['CN_EQ']['lastSourceSuccessAt']=stamp
            health['calendars']['CN_EQ']['lastVerifiedAt']=stamp
            hp.write_bytes(runtime.canonical(health))
            sync.sync(root,now=now+timedelta(days=2),fetch=fail)
            held=json.loads(hp.read_bytes())['calendars']['CN_EQ']
            self.assertEqual(held['lastSourceSuccessAt'],stamp)
            self.assertEqual(held['lastVerifiedAt'],stamp)

    def test_incomplete_and_changed_format_never_parse_as_no_holidays(self):
        for parser in (sync.parse_china,sync.parse_china_english,sync.parse_nyse,sync.parse_nasdaq,sync.parse_jpx):
            with self.subTest(parser=parser.__name__),self.assertRaises(ValueError):parser('<html>unexpected error page</html>')

    def test_cn_annual_parser_holiday_ranges(self):
        rows=[('元旦',1,1,1,3),('春节',2,15,2,23),('清明节',4,4,4,6),('劳动节',5,1,5,5),('端午节',6,19,6,21),('中秋节',9,25,9,27),('国庆节',10,1,10,7)]
        html='<h1>2026年休市安排</h1>'+''.join(f'<p>{n}：{m}月{d}日（星期一）至{m2}月{d2}日（星期二）休市</p>' for n,m,d,m2,d2 in rows)
        parsed=sync.parse_china(html)['2026']
        self.assertEqual(parsed['holidays'],self.snapshot['data']['calendars']['CN_EQ']['years']['2026']['holidays'])

    def test_revision_stays_frozen_after_current_entry_changes(self):
        version=self.snapshot['revision'];calls=[]
        def get(path):calls.append(path);return self.raw
        self.assertEqual(runtime.remote(version,fetch=get)[1]['revision'],version)
        self.assertEqual(calls,['calendars/releases/'+version+'.json'])

    def test_nasdaq_year_header_is_not_a_holiday_row(self):
        record=self.snapshot['data']['calendars']['US_EQ']['years']['2026']
        html='<h1>U.S. Equity and Options Markets Holiday Schedule 2026</h1><table><tr><th>2026</th><th>Holiday</th><th>Status</th></tr>'
        for day in record['holidays']:
            html+='<tr><td>'+date.fromisoformat(day).strftime('%B %d, %Y')+'</td><td>Holiday</td><td>Closed</td></tr>'
        for day in record['overrides']:
            html+='<tr><td>'+date.fromisoformat(day).strftime('%B %d, %Y')+'</td><td>Early Close</td><td>1:00 p.m.</td></tr>'
        parsed=sync.parse_nasdaq(html+'</table>')['2026']
        self.assertEqual(set(parsed['holidays']),set(record['holidays']))
        self.assertEqual(parsed['overrides'],record['overrides'])

    def test_jpx_annual_heading_and_weekend_rows(self):
        record=self.snapshot['data']['calendars']['JP_EQ']['years']['2027']
        html='<h2><span>2027</span></h2><table>'
        for day in [*record['holidays'],'2027-01-02','2027-01-03']:
            html+='<tr><td>'+date.fromisoformat(day).strftime('%b. %-d (%a.)')+'</td><td>Holiday</td></tr>'
        parsed=sync.parse_jpx(html+'</table>')['2027']
        self.assertEqual(set(parsed['holidays']),set(record['holidays']))

    def test_nyse_multi_year_table_keeps_2028_new_year_exception(self):
        labels=['New Year','Martin Luther','Washington','Good Friday','Memorial','Juneteenth','Independence','Labor','Thanksgiving','Christmas']
        years=self.snapshot['data']['calendars']['US_EQ']['years']
        html='<table><tr><th>Holiday</th><th>2026</th><th>2027</th><th>2028</th></tr>'
        names=['元旦','马丁·路德·金纪念日','华盛顿诞辰日','耶稣受难日','阵亡将士纪念日','六月节','独立日','劳动节','感恩节','圣诞节']
        for label,name in zip(labels,names):
            html+='<tr><td>'+label+'</td>'
            for year in ('2026','2027','2028'):
                day=next((d for d,n in years[year]['holidays'].items() if n==name),None)
                html+='<td>'+(date.fromisoformat(day).strftime('%A, %B %d') if day else '—*')+'</td>'
            html+='</tr>'
        html+='</table><p>Each market will close early at 1:00 p.m. on '
        html+=', '.join(date.fromisoformat(d).strftime('%A, %B %d, %Y') for y in years.values() for d in y['overrides'])
        html+='. All times are Eastern Time.</p>'
        parsed=sync.parse_nyse(html)
        self.assertNotIn('2028-01-01',parsed['2028']['holidays'])
        self.assertNotIn('2027-12-31',parsed['2027']['holidays'])
        self.assertEqual(parsed['2027']['overrides'],years['2027']['overrides'])


if __name__=='__main__':unittest.main()
