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

    def test_czce_english_dates_and_no_night_sessions_must_match_chinese_annual(self):
        import re
        chinese=fixture('shfe-2026.json')['text'];expected,nights=a.parse_china_futures(chinese)
        groups=("New Year's Day",'Spring Festival','Qingming Festival','Labor Day','Dragon Boat Festival','Mid-Autumn Festival','National Day')
        text='Notice on Trading Schedule during National Holidays for Year 2026\n'
        for i,(name,label) in enumerate(zip(groups,s.LABELS)):
            dates=sorted(d for d,v in expected['2026']['holidays'].items() if v==label)
            def stamp(value):return date.fromisoformat(value).strftime('%B %d, %Y (%A)')
            text+=f'{i+1}. {name}: The market will be closed during holidays from {stamp(dates[0])} to {stamp(dates[-1])}, and will be opened again. There will be no night trading session on {stamp(nights[i])}.\n'
        parsed,actual_nights=a.parse_china_futures_english(text)
        self.assertEqual(set(expected['2026']['holidays']),set(parsed['2026']['holidays']))
        self.assertEqual(nights,actual_nights)
        with self.assertRaises(ValueError):a.parse_china_futures_english(text.replace('There will be no night trading session','No notice',1))

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

    def test_a50_only_withdraws_future_claims_and_wrong_provider_product_stays_rejected(self):
        cal=runtime.validate(runtime.seed_path().read_bytes())['data']['calendars']['A50_CFD']
        html='富时中国A50期货CFD <script>futures_app.code = "CHA50CFD";</script>'
        values=a.a50_unknown_provider_calendar(cal,html,'2026-10-24')
        self.assertIn('2026-10-24',values['2026']['unknownDates'])
        self.assertNotIn('2026-10-23',values['2026']['unknownDates'])
        self.assertIn('2027-12-31',values['2027']['unknownDates'])
        self.assertFalse(values['2027']['holidays'])
        with self.assertRaises(ValueError):a.a50_unknown_provider_calendar(cal,html.replace('CHA50CFD','MCA'),'2026-10-24')

    def test_a50_provider_uncertainty_changes_no_history_or_other_market(self):
        import calendar_ops as ops
        before=runtime.validate(runtime.seed_path().read_bytes());data=deepcopy(before['data'])
        cal=data['calendars']['A50_CFD']
        cal['years'].update(a.a50_unknown_provider_calendar(cal,'富时中国A50期货CFD futures_app.code="CHA50CFD"','2026-10-24'))
        after=runtime.validate(runtime.canonical(data));report=ops.impact(before,after,date(2026,10,23))
        self.assertEqual(0,report['historicalChangedDays']);self.assertEqual({'A50_CFD'},set(report['byCalendar']))
        self.assertTrue(all(c['after']['kind']=='UNKNOWN' for c in report['changes']))

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
        decorated=title.replace('for 2026','for <b>2026</b>').replace('Segment,','Segment ,')
        self.assertEqual(13,len(a.parse_bse(decorated+table(rows))['2026']['holidays']))
        with self.assertRaises(ValueError):a.parse_bse(title.replace('Equity','Commodity')+table(rows))
        rows[0][3]='Sunday'
        with self.assertRaises(ValueError):a.parse_bse(title+table(rows))

    def test_sgx_actual_contract_codes_distinguish_expiry_from_closure(self):
        data=fixture('sgx-nikkei-2026.json')
        record=a.parse_sgx(json.dumps(data))['2026']
        self.assertEqual({'2026-01-01'},set(record['holidays']))
        self.assertNotIn('2026-01-08',record['holidays'])
        self.assertNotIn('2026-01-09',record['holidays'])
        self.assertNotIn('2026-04-03',record['holidays'])
        self.assertNotIn('2026-12-25',record['holidays'])
        data['months'].pop()
        with self.assertRaises(ValueError):a.parse_sgx(json.dumps(data))

    def test_sgx_missing_expiry_contract_identity_or_wrong_vector_fails_closed(self):
        from calendar_pdf import mark_kind
        from types import SimpleNamespace as Point
        cross={'items':[('l',Point(x=0,y=0),Point(x=1,y=1)),('l',Point(x=1,y=0),Point(x=0,y=1))]}
        self.assertEqual('closed',mark_kind(cross))
        self.assertEqual('expiry',mark_kind({'items':[('re',None)]}))
        self.assertEqual('lastTrading',mark_kind({'items':[('l',None,None)]*3}))
        cross['items'][1]=('l',Point(x=0,y=2),Point(x=1,y=3))
        with self.assertRaises(ValueError):mark_kind(cross)
        for mutate in ('contract','events'):
            data=fixture('sgx-nikkei-2026.json')
            if mutate=='contract':data['contract']='NKO'
            else:data['months'][0]['events']=[e for e in data['months'][0]['events'] if e['kind']!='expiry']
            with self.assertRaises(ValueError):a.parse_sgx(json.dumps(data))

    def test_declared_provider_gb_charset_is_decoded_without_losing_source_hash(self):
        raw='<meta charset="gb2312"><h1>富时中国A50期货CFD</h1>'.encode('gb18030')
        self.assertIn('富时中国A50期货CFD',s.source_text(raw))

    def test_bse_master_equity_section_excludes_weekend_table_and_keeps_muhurat_unknown(self):
        base=runtime.validate(runtime.seed_path().read_bytes())['data']['calendars']['IN_EQ']['years']['2026']
        rows=[['Sr. No.','Holiday','Date','Day']]
        for d,label in sorted(base['holidays'].items()):
            day=date.fromisoformat(d)
            rows.append([str(len(rows)),'Republic Day' if d=='2026-01-26' else 'Christmas' if d=='2026-12-25' else label,
                         day.strftime('%B %d, %Y'),day.strftime('%A')])
        obj={'format':'bse_equity_master_tables_v1','texts':['2.2 Trading Holidays for 2026. Muhurat Trading will be conducted on Sunday, November 08, 2026.'],
             'tables':[rows,[['1','Weekend holiday','February 15, 2026','Sunday']]]}
        record=a.parse_bse_master(json.dumps(obj))['2026']
        self.assertEqual(set(base['holidays']),set(record['holidays']))
        self.assertEqual(['2026-11-08'],record['unknownDates'])
        self.assertNotIn('2026-02-15',record['holidays'])
        obj['format']='bse_commodity_master_tables_v1'
        with self.assertRaises(ValueError):a.parse_bse_master(json.dumps(obj))

    def test_bse_actual_beta_and_split_pdf_cells_agree_without_currency_holidays(self):
        facts=fixture('bse-2026-official-tables.json')
        heading='Trading Holidays for 2026 - Equity Segment , Equity Derivative Segment'
        note='<p>Muhurat Trading will be conducted on Sunday,\n November 08, 2026.</p>'
        html=heading+table(facts['equityRows'])+note+'<h2>Trading holidays for 2026 - Currency Derivatives</h2>'+table(facts['foreignSegmentRows'])
        first=a.parse_bse(html)['2026']
        second=a.parse_bse_master(json.dumps({'format':'bse_equity_master_tables_v1',
            'texts':['2.2 Trading Holidays for 2026. Muhurat Trading will be conducted on Sunday,\n November 08, 2026.'],
            'tables':[facts['masterRows']]}))['2026']
        self.assertEqual(16,len(first['holidays']))
        self.assertEqual(set(first['holidays']),set(second['holidays']))
        self.assertEqual(['2026-11-08'],first['unknownDates'])
        self.assertEqual(first['unknownDates'],second['unknownDates'])
        self.assertNotIn('2026-02-19',first['holidays'])
        self.assertNotIn('2026-04-01',first['holidays'])

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

    def test_reviewed_china_fallback_is_exact_year_source_and_digest_bound(self):
        import shutil
        spec=s.SOURCES['CN_FUTURES'];venue='DCE';url=spec['urls'][2]
        now=datetime(2026,10,23,15,tzinfo=ZoneInfo('Asia/Shanghai'))
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/spec['reviewedFallback'];path.parent.mkdir(parents=True)
            shutil.copy2(s.ROOT/spec['reviewedFallback'],path)
            data,nights,proof=a.reviewed_china_futures(root,spec,venue,url,now)
            expected,actual_nights=a.parse_china_futures(fixture('shfe-2026.json')['text'])
            self.assertEqual(set(expected['2026']['holidays']),set(data['2026']['holidays']))
            self.assertEqual(actual_nights,nights);self.assertEqual('reviewed_official_index',proof['mode'])
            with self.assertRaises(ValueError):a.reviewed_china_futures(root,spec,venue,'https://wrong.example/',now)
            with self.assertRaises(ValueError):a.reviewed_china_futures(root,spec,venue,url,now.replace(year=2027))
            path.write_bytes(path.read_bytes()+b' ')
            with self.assertRaises(ValueError):a.reviewed_china_futures(root,spec,venue,url,now)

    def test_china_live_parses_and_reviewed_fallback_are_distinct_and_format_changes_stay_held(self):
        import shutil
        spec=s.SOURCES['CN_FUTURES'];text=fixture('shfe-2026.json')['text']
        for changed_format in (False,True):
            with tempfile.TemporaryDirectory() as td:
                root=Path(td);(root/'calendars/releases').mkdir(parents=True)
                raw=runtime.seed_path().read_bytes();revision=runtime.validate(raw)['revision']
                path='calendars/releases/'+revision+'.json';(root/path).write_bytes(raw)
                (root/'calendars/current.json').write_bytes(runtime.canonical({'schemaVersion':1,'sha256':revision,'path':path}))
                fallback=root/spec['reviewedFallback'];fallback.parent.mkdir(parents=True)
                shutil.copy2(s.ROOT/spec['reviewedFallback'],fallback)
                def fetch(url):
                    if url in spec['urls'][:2]:return text.encode()
                    if changed_format and url==spec['urls'][2]:return b'Official annual page with changed format'
                    raise OSError('official source unavailable')
                s.sync(root,force=True,fetch=fetch,now=datetime(2026,10,23,15,tzinfo=ZoneInfo('Asia/Shanghai')))
                state=json.loads((root/'calendars/health.json').read_bytes())['calendars']['CN_FUTURES']
                self.assertEqual('parsed',state['venueValidation']['SHFE']['status'])
                if changed_format:
                    self.assertEqual('held',state['venueValidation']['DCE']['status'])
                    self.assertEqual('held_preserving_active_calendar',state['status'])
                else:
                    self.assertEqual('verified_with_reviewed_fallback',state['status'])
                    self.assertEqual(['DCE','CZCE','GFEX'],state['reviewedFallbackVenues'])
                    self.assertNotIn('lastSourceSuccessAt',state)
                    self.assertEqual('unavailable',state['sourceResults'][2]['status'])
                self.assertEqual(revision,json.loads((root/'calendars/current.json').read_bytes())['sha256'])


if __name__=='__main__':unittest.main()
