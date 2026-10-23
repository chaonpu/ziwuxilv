"""Normalized facts from actual official pages, public forms and PDF tables."""
from copy import deepcopy
from datetime import date, datetime
from html import escape
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import calendar_adapters as a
import calendar_sync as s
import calendar_runtime as runtime

FIX=Path(__file__).with_name('fixtures')
def fixture(name):return json.loads((FIX/name).read_text())
def table(rows):return '<table>'+''.join('<tr>'+''.join('<td>'+escape(c)+'</td>' for c in r)+'</tr>' for r in rows)+'</table>'
def hk(year):
    data=fixture('hkex-'+str(year)+'.json')
    text='Hong Kong Securities Market Holiday Schedule for Year '+str(year)+'\nSecurities Market is closed on Saturdays, Sundays'
    for d in data['closed']:
        day=date.fromisoformat(d);text+='\n'+day.strftime('%d %B %Y %A')+' Public Holiday'
    text+='\nThe following trading days are half-day trading day:\n'
    for d in data['halfDays']:text+=date.fromisoformat(d).strftime('%d %B %Y (%A)')+' - Eve\n'
    return text
def b3():
    data=fixture('b3-2026.json');result='<h2>Market Calendar 2026</h2>Holiday calendar for 2026'
    for month,rows in data['months'].items():result+='<a href="#x">'+month+'</a>'+table(rows)
    return result
def ice():
    data=fixture('ice-brent-2026-2027.json');text='IFEU Trading Schedule: Oil, Utilities\n'
    for year,rows in data['years'].items():
        text+=year+' Trading Schedule\n'
        for row in rows:
            text+=date.fromisoformat(row['date']).strftime('%A %d %B')+'\n'+row['status']+'\n'
            if row['otherProductsClosed']:text+='IFEU Agricultural Commodities Closed\n'
    text+='TRADING SCHEDULE KEY\n'
    for d in data['appendixDates']:text+=date.fromisoformat(d).strftime('%d %B %Y')+'\n'
    return text
def cme():return '<h2>2026 CME Globex Trading Schedule</h2>'+table(fixture('cme-globex-2026.json')['rows'])+'<h2>BrokerTec calendar</h2>'

class ExtendedAdaptersTests(unittest.TestCase):
    def test_hk_actual_years_and_separately_verified_half_day_clock(self):
        for year,count in ((2026,14),(2027,13)):
            parsed=a.parse_hkex(hk(year),'Half Day Trading Closing Auction 12:08 12:10')[str(year)]
            self.assertEqual(count,len(parsed['holidays']));self.assertEqual(3,len(parsed['overrides']))
            self.assertTrue(all(v=={'halfDay':True,'closeMinute':730} for v in parsed['overrides'].values()))
        with self.assertRaises(ValueError):a.parse_hkex(hk(2027),'Derivatives 12:30')

    def test_hk_truncated_year_and_bad_weekday_reject(self):
        with self.assertRaises(ValueError):a.parse_hkex(hk(2027).replace('31 December 2027 (Friday) - Eve',''),'Half Day Trading 12:08 12:10')
        with self.assertRaises(ValueError):a.parse_hkex(hk(2027).replace('01 January 2027 Friday','01 January 2027 Monday'),'Half Day Trading 12:08 12:10')

    def test_b3_real_equity_scope_excludes_foreign_clearing_bank_holidays(self):
        parsed=a.parse_b3(b3())['2026']
        self.assertEqual(14,len(parsed['holidays']))
        self.assertNotIn('2026-01-19',parsed['holidays'])
        self.assertNotIn('2026-11-26',parsed['holidays'])
        self.assertEqual({'openMinute':780},parsed['overrides']['2026-02-18'])
        self.assertIn('2026-12-31',parsed['holidays'])
        with self.assertRaises(ValueError):a.parse_b3(b3().replace('<a href="#x">December</a>',''))

    def test_ice_brent_does_not_inherit_other_products_closures_or_guess_hours(self):
        parsed=a.parse_ice_brent(ice())
        self.assertEqual({'2026','2027'},set(parsed))
        self.assertEqual({'2027-01-01','2027-03-26'},set(parsed['2027']['holidays']))
        self.assertNotIn('2026-12-28',parsed['2026']['holidays'])
        self.assertIn('2026-12-28',parsed['2026']['unknownDates'])
        self.assertNotIn('2027-03-29',parsed['2027']['holidays'])
        self.assertIn('2027-11-26',parsed['2027']['unknownDates'])
        self.assertIn('2027-12-24',parsed['2027']['unknownDates'])
        self.assertFalse(parsed['2027']['overrides'])
        with self.assertRaises(ValueError):a.parse_ice_brent('ICE homepage')

    def test_cme_global_holidays_are_unknown_instead_of_uniform_product_closures(self):
        parsed=a.parse_cme_affected_days(cme())
        self.assertEqual({'2026'},set(parsed));self.assertFalse(parsed['2026']['holidays'])
        self.assertIn('2026-11-26',parsed['2026']['unknownDates'])
        self.assertNotIn('2027',parsed)
        with self.assertRaises(ValueError):a.parse_cme_affected_days(cme().replace('2026 CME Globex Trading Schedule','2026 Trading Floor Schedule'))

    def test_china_futures_requires_all_seven_night_suspensions(self):
        text=fixture('shfe-2026.json')['text'];parsed,nights=a.parse_china_futures(text)
        self.assertEqual(7,len(nights));self.assertIn('2025-12-31',nights)
        self.assertIn('2026-09-30',nights)
        self.assertIn('2026-10-07',parsed['2026']['holidays'])
        with self.assertRaises(ValueError):a.parse_china_futures(text.replace('9月30日（星期三）晚上不进行夜盘交易。',''))

    def test_krx_actual_annual_public_form_data_is_bound_to_year_and_cash_market(self):
        for year,count in ((2026,17),(2027,16)):
            data=fixture('krx-'+str(year)+'.json');parsed=a.parse_krx(json.dumps(data),str(year))
            self.assertEqual(count,len(parsed[str(year)]['holidays']))
            self.assertIn(str(year)+'-12-31',parsed[str(year)]['holidays'])
            with self.assertRaises(ValueError):a.parse_krx(json.dumps(data),str(year+1))
        data=fixture('krx-2027.json');data['response']['block1'][0]['kr_dy_tp']='Monday'
        with self.assertRaises(ValueError):a.parse_krx(json.dumps(data),'2027')
        self.assertEqual({},a.parse_krx(json.dumps({'year':2028,'market':'KRX_CASH','response':{'block1':[]}}),'2028'))

    def test_scope_guards_cannot_promote_cfd_or_calendar_image_to_full_year(self):
        for adapter in ('blocked-a50-provider','blocked-sgx-product'):
            with self.assertRaises(ValueError):a.guarded_source(adapter,['SGX Nikkei'])

    def test_saudi_lunar_estimates_stay_unknown_and_final_dates_are_required(self):
        rows=[['22/02/2027','Founding Day'],['23/09/2027','National Day'],
              ['07/03/2027 11/03/2027','Eid Al Fitr end of trading day 04/03/2027. Trading will resume after the holiday on 14/03/2027 UMM AL-QURA'],
              ['16/05/2027 20/05/2027','Eid Al Adha end of trading day 13/05/2027. Trading will resume after the holiday on 23/05/2027 UMM AL-QURA']]
        html='Saudi Exchange Holiday Calendar'+table(rows)
        parsed=a.parse_saudi(html,2026)['2027']
        self.assertIn('2027-02-22',parsed['holidays'])
        self.assertIn('2027-03-07',parsed['unknownDates'])
        self.assertNotIn('2027-03-07',parsed['holidays'])
        with self.assertRaises(ValueError):a.parse_saudi(html.replace('Trading will resume after the holiday on','Estimated return'),2026)
        with self.assertRaises(ValueError):a.parse_saudi('Saudi Exchange Holiday Calendar'+table(rows[:3]),2026)

    def test_bse_requires_equity_table_and_checks_weekday_before_first_enablement(self):
        days=['2026-01-26','2026-03-03','2026-03-26','2026-03-31','2026-04-03','2026-04-14','2026-05-01',
              '2026-05-28','2026-06-26','2026-09-14','2026-10-02','2026-10-20','2026-12-25']
        rows=[[str(i+1),'Republic Day' if i==0 else 'Christmas' if i==12 else 'Holiday',
               date.fromisoformat(d).strftime('%d-%b-%y'),date.fromisoformat(d).strftime('%A')] for i,d in enumerate(days)]
        title='Trading Holidays for 2026 - Equity Segment, Equity Derivative Segment'
        self.assertEqual(13,len(a.parse_bse(title+table(rows))['2026']['holidays']))
        with self.assertRaises(ValueError):a.parse_bse(title.replace('Equity','Commodity')+table(rows))
        rows[0][3]='Sunday'
        with self.assertRaises(ValueError):a.parse_bse(title+table(rows))

    def test_source_transfers_reject_cross_host_redirect_before_following(self):
        import urllib.request
        request=urllib.request.Request('https://www.hkex.com.hk/calendar')
        with self.assertRaises(ValueError):s.SameHostRedirect().redirect_request(request,None,302,'',{},'https://example.com/calendar')

    def test_live_facts_publish_future_year_even_when_current_year_requires_review(self):
        raw=runtime.seed_path().read_bytes();snapshot=runtime.validate(raw)
        revision=snapshot['revision']
        sources={s.SOURCES['HK_EQ']['urls'][0]:hk(2026),s.SOURCES['HK_EQ']['urls'][1]:hk(2027),
                 s.SOURCES['HK_EQ']['urls'][2]:'Half Day Trading 12:08 12:10',
                 s.SOURCES['BR_EQ']['urls'][0]:b3(),s.SOURCES['ICE_BRENT']['urls'][0]:ice(),
                 s.SOURCES['CME_FUTURES']['urls'][0]:cme()}
        for year in (2026,2027):
            url=s.SOURCES['KR_EQ']['urls'][0]+'?search_bas_yy='+str(year)
            sources[url]=json.dumps(fixture('krx-'+str(year)+'.json'))
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'calendars/releases').mkdir(parents=True)
            index={'schemaVersion':1,'sha256':revision,'path':'calendars/releases/'+revision+'.json'}
            (root/'calendars/current.json').write_bytes(runtime.canonical(index));(root/index['path']).write_bytes(raw)
            def fetch(url):
                if url not in sources:raise OSError('source not in fixture')
                return sources[url].encode()
            result=s.sync(root,force=True,fetch=fetch,now=datetime(2026,10,23,15,tzinfo=ZoneInfo('Asia/Shanghai')))
            self.assertIn('KR_EQ:2027',result['changedCalendars'])
            self.assertIn('ICE_BRENT:2027',result['changedCalendars'])
            health=json.loads((root/'calendars/health.json').read_bytes())
            self.assertIn('2026',health['calendars']['ICE_BRENT']['heldYears'])
            new_index=json.loads((root/'calendars/current.json').read_bytes())
            active=runtime.validate((root/new_index['path']).read_bytes())
            self.assertEqual('CLOSED',runtime.day_rule(active,'KR_EQ','2027-02-08')['kind'])
            self.assertEqual('UNKNOWN',runtime.day_rule(active,'ICE_BRENT','2027-12-24')['kind'])
            self.assertTrue(list((root/'calendars/candidates').glob('*.impact.json')))


if __name__=='__main__':unittest.main()
