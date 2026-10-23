"""A50 reference feeds are bounded, disclosed and preserve historical dates."""
from copy import deepcopy
from datetime import date,datetime,timedelta
from html import escape
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import calendar_a50 as a
import calendar_sync as s
import calendar_runtime as r
import calendar_ops as ops
import calendar_watchdog as watch
from calendar_adapters import a50_unknown_provider_calendar

NOW=datetime(2026,10,23,22,tzinfo=ZoneInfo('Asia/Shanghai'))
FACTS=json.loads((Path(__file__).with_name('fixtures')/'a50-forex-reference-20261023.json').read_text())
IDENTITY='富时中国A50期货CFD <script>futures_app.code="CHA50CFD";</script>'
SPEC='\n'.join(FACTS['contractRows'])


def html(row=None,header=None):
    rows=[header or FACTS['header'],row or FACTS['row']]
    return '交易时间和节假日安排 即将到来的节假日 下表所示时间均为北京时间(GMT+8):<table>'+''.join(
        '<tr>'+''.join('<td>'+escape(cell)+'</td>' for cell in row)+'</tr>' for row in rows)+'</table>© FOREX.COM 2026'


def baseline():
    data=r.validate(r.seed_path().read_bytes())['data']
    cal=data['calendars']['A50_CFD'];cal['years'].update(a50_unknown_provider_calendar(cal,IDENTITY,'2026-10-24'))
    return r.validate(r.canonical(data))


class A50ReferenceTests(unittest.TestCase):
    def test_actual_broker_rows_resolve_only_published_rolling_window(self):
        ref=a.reference_schedule(IDENTITY,html(),SPEC,NOW.date())
        self.assertEqual(('2026-10-24','2026-11-27'),(ref['start'],ref['end']))
        self.assertEqual('third_party_reference',ref['evidenceLevel']);self.assertFalse(ref['providerScheduleVerified'])
        self.assertEqual(12,len(ref['exceptions']));self.assertEqual({'NORMAL'},set(ref['exceptions'].values()))

    def test_wrong_sina_product_wrong_weekly_spec_and_wrong_index_rejected(self):
        for identity,body,spec in ((IDENTITY.replace('CHA50CFD','MCA'),html(),SPEC),
                (IDENTITY,html(),SPEC.replace('05:15','04:00',1)),
                (IDENTITY,html([c.replace('中国A50指数','明晟中国A50互联互通指数') for c in FACTS['row']]),SPEC)):
            with self.assertRaises(ValueError):a.reference_schedule(identity,body,spec,NOW.date())

    def test_stale_year_date_order_and_truncated_table_rejected(self):
        for body,today in ((html(),date(2026,12,1)),(html(),date(2027,10,23)),
                (html(header=[FACTS['header'][0]]+list(reversed(FACTS['header'][1:]))),NOW.date()),
                (html(row=FACTS['row'][:-1]),NOW.date())):
            with self.assertRaises(ValueError):a.reference_schedule(IDENTITY,body,SPEC,today)

    def test_multiple_conflicting_product_tables_are_not_merged(self):
        row=list(FACTS['row']);row[-1]='休市'
        with self.assertRaises(ValueError):a.reference_schedule(IDENTITY,html()+html(row),SPEC,NOW.date())

    def test_closed_and_partial_day_status_do_not_become_normal(self):
        row=list(FACTS['row']);row[1]='休市';row[2]='关市 2AM ND'
        ref=a.reference_schedule(IDENTITY,html(row),SPEC,NOW.date())
        self.assertEqual('CLOSED',ref['exceptions']['2026-10-26'])
        self.assertEqual('UNKNOWN',ref['exceptions']['2026-10-30'])

    def test_only_a50_future_dates_change_and_other_years_stay_unknown(self):
        before=baseline();data=deepcopy(before['data']);ref=a.reference_schedule(IDENTITY,html(),SPEC,NOW.date())
        data['calendars']['A50_CFD']['years'].update(a.apply_reference(data['calendars']['A50_CFD'],ref,NOW.date()))
        after=r.validate(r.canonical(data));report=ops.impact(before,after,NOW.date())
        self.assertEqual({'A50_CFD':35},report['byCalendar']);self.assertEqual(0,report['historicalChangedDays'])
        self.assertEqual('CLOSED',r.day_rule(after,'A50_CFD','2026-10-24')['kind'])
        self.assertEqual('NORMAL',r.day_rule(after,'A50_CFD','2026-10-26')['kind'])
        self.assertEqual('NORMAL',r.day_rule(after,'A50_CFD','2026-11-27')['kind'])
        for day in ('2026-11-28','2027-01-04','2028-01-03'):
            self.assertEqual('UNKNOWN',r.day_rule(after,'A50_CFD',day)['kind'])

    def test_shorter_new_window_retracts_previously_known_future_reference_days(self):
        before=baseline()['data']['calendars']['A50_CFD'];ref=a.reference_schedule(IDENTITY,html(),SPEC,NOW.date())
        before['years'].update(a.apply_reference(before,ref,NOW.date()))
        ref=deepcopy(ref);ref['end']='2026-11-20'
        record=a.apply_reference(before,ref,NOW.date())['2026']
        self.assertIn('2026-11-27',record['unknownDates']);self.assertNotIn('2026-11-27',record['overrides'])
        self.assertIn('2026-11-20',record['overrides'])

    def test_window_cannot_be_expanded_by_input_or_change_the_current_day(self):
        ref=a.reference_schedule(IDENTITY,html(),SPEC,NOW.date());ref['start']='2026-10-23'
        with self.assertRaises(ValueError):a.apply_reference(baseline()['data']['calendars']['A50_CFD'],ref,NOW.date())
        ref['start']='2026-10-24';ref['end']='2027-01-31'
        with self.assertRaises(ValueError):a.apply_reference(baseline()['data']['calendars']['A50_CFD'],ref,NOW.date())

    def test_reference_is_daily_outside_q4_and_monitor_uses_effective_end(self):
        cal=baseline()['data']['calendars']['A50_CFD'];now=NOW.replace(month=7)
        state={'adapter':'a50-cfd-reference','lastAttemptAt':now.isoformat(),'parsedYears':['2026']}
        self.assertEqual((True,'rolling_reference_daily'),s.should_check(cal,state,now+timedelta(days=1),adapter='a50-cfd-reference'))
        snap=baseline();ref=a.reference_schedule(IDENTITY,html(),SPEC,NOW.date())
        snap['data']['calendars']['A50_CFD']['years'].update(a.apply_reference(cal,ref,NOW.date()))
        snap=r.validate(r.canonical(snap['data']))
        health={'schemaVersion':1,'activeRevision':snap['revision'],'calendars':{k:{'lastAttemptAt':NOW.isoformat(),'adapter':'test'} for k in snap['data']['calendars']}}
        health['calendars']['A50_CFD']['adapter']='a50-cfd-reference'
        report=watch.assess(snap,health,[],NOW)
        item=next(i for i in report['calendars'] if i['calendarId']=='A50_CFD')
        self.assertEqual(35,item['coverageDaysRemaining']);self.assertEqual(48,item['limitHours'])
        warning=next(w for w in ops.coverage(snap,NOW.date()) if w['calendarId']=='A50_CFD')
        self.assertEqual('rolling_reference_window_ending',warning['reason'])

    def test_real_sync_publishes_disclosed_reference_and_failure_preserves_bytes(self):
        spec=s.SOURCES['A50_CFD'];sources=dict(zip(spec['urls'],(IDENTITY,html(),SPEC)))
        with tempfile.TemporaryDirectory() as td,patch.dict(s.SOURCES,{'A50_CFD':spec},clear=True):
            root=Path(td);before=baseline();raw=r.canonical(before['data']);rev=before['revision']
            (root/'calendars/releases').mkdir(parents=True);path='calendars/releases/'+rev+'.json'
            (root/path).write_bytes(raw);(root/'calendars/current.json').write_bytes(r.canonical({'schemaVersion':1,'sha256':rev,'path':path}))
            def fetch(url):
                if url not in sources:raise OSError('other market unavailable in isolated test')
                return sources[url].encode()
            result=s.sync(root,force=True,now=NOW,fetch=fetch)
            self.assertEqual(['A50_CFD:2026'],result['changedCalendars'])
            state=json.loads((root/'calendars/health.json').read_bytes())['calendars']['A50_CFD']
            self.assertEqual('published_third_party_reference',state['status']);self.assertFalse(state['providerScheduleVerified'])
            self.assertEqual('2026-11-27',state['referenceValidThrough'])
            active=ops.snapshot(root)[1];self.assertEqual(0,ops.impact(before,active,NOW.date())['historicalChangedDays'])
            def offline(url):raise OSError('network unavailable')
            again=s.sync(root,force=True,now=NOW+timedelta(days=1),fetch=offline)
            self.assertEqual(result['activeRevision'],again['activeRevision'])
            self.assertEqual('held_preserving_active_calendar',again['results']['A50_CFD'])
            held=json.loads((root/'calendars/health.json').read_bytes())['calendars']['A50_CFD']
            self.assertEqual('2026-11-27',held['referenceValidThrough'])


if __name__=='__main__':unittest.main()
