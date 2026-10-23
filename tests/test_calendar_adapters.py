"""Small normalized facts from actual exchange responses fetched 2026-10-23.

Fixtures contain only annual table cells/JSON, never full exchange webpages.
"""
from copy import deepcopy
from datetime import datetime
from html import escape
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import calendar_sync as sync
import calendar_runtime as runtime

FIX=Path(__file__).with_name('fixtures')


def fixture(name):return json.loads((FIX/name).read_text())


def table(rows):return '<table>'+''.join('<tr>'+''.join('<td>'+escape(c)+'</td>' for c in r)+'</tr>' for r in rows)+'</table>'


class AnnualAdaptersTests(unittest.TestCase):
    def test_twse_two_languages_agree_and_open_days_are_not_holidays(self):
        first=sync.parse_twse(json.dumps(fixture('twse-2026-zh.json')))
        second=sync.parse_twse(json.dumps(fixture('twse-2026-en.json')),english=True)
        self.assertEqual(set(first['2026']['holidays']),set(second['2026']['holidays']))
        self.assertEqual(len(first['2026']['holidays']),18)
        self.assertNotIn('2026-02-11',first['2026']['holidays'])
        self.assertNotIn('2026-02-23',first['2026']['holidays'])
        self.assertIn('2026-02-12',first['2026']['holidays'])

    def test_twse_unpublished_is_explicit_and_truncated_or_cross_year_is_rejected(self):
        self.assertEqual(sync.parse_twse(json.dumps(fixture('twse-2027-unpublished.json'))),{})
        data=fixture('twse-2026-en.json');data['data']=data['data'][:3]
        with self.assertRaises(ValueError):sync.parse_twse(json.dumps(data),english=True)
        data=fixture('twse-2026-en.json');data['data'][0][0]='2027-01-01'
        with self.assertRaises(ValueError):sync.parse_twse(json.dumps(data),english=True)

    def test_lse_rolling_partial_year_is_not_promoted_to_full_year(self):
        parsed=sync.parse_lse(table(fixture('lse-rolling.json')['rows']))
        self.assertEqual(set(parsed),{'2027','2028'})
        self.assertIn('2028-12-22',parsed['2028']['overrides'])
        self.assertEqual(parsed['2027']['overrides']['2027-12-24'],{'closeMinute':750})
        self.assertEqual(len(parsed['2027']['holidays']),8)

    def test_lse_otc_availability_never_makes_closed_cash_market_open(self):
        rows=fixture('lse-rolling.json')['rows']
        data=sync.parse_lse(table(rows))['2027']
        self.assertIn('2027-05-31',data['holidays'])
        bad=deepcopy(rows)
        next(r for r in bad if r[0]=='Friday 26 March 2027')[2]='Available as normal'
        with self.assertRaises(ValueError):sync.parse_lse(table(bad))

    def test_xetra_published_years_keep_last_session_unknown_until_circular(self):
        data=fixture('xetra-annual.json')
        parsed=sync.parse_xetra(data['scope']+table(data['rows']))
        self.assertEqual(set(parsed),{str(y) for y in range(2026,2033)})
        self.assertIn('2027-03-26',parsed['2027']['holidays'])
        self.assertEqual(parsed['2026']['unknownDates'],['2026-12-30'])
        self.assertEqual(parsed['2028']['unknownDates'],['2028-12-29'])
        with self.assertRaises(ValueError):sync.parse_xetra(table(data['rows']))

    def test_euronext_selects_paris_instead_of_another_exchange_or_dublin_hours(self):
        data=fixture('euronext-paris.json')
        parsed=sync.parse_euronext_paris('Calendar of business days'+''.join(table(g) for g in data['tables']))
        self.assertEqual(len(parsed['2026']['holidays']),5)
        self.assertEqual(set(parsed['2026']['unknownDates']),{'2026-12-24','2026-12-31'})
        self.assertNotIn('2026-05-04',parsed['2026']['holidays'])
        self.assertFalse(parsed['2023']['unknownDates'])

    def test_error_homepage_and_missing_table_cannot_become_empty_year(self):
        for parser in (sync.parse_xetra,sync.parse_lse,sync.parse_euronext_paris,sync.parse_twse):
            with self.subTest(parser=parser.__name__),self.assertRaises(ValueError):parser('<html>Error page</html>')

    def test_paris_weekend_heavy_year_has_three_closures_but_year_end_cannot_be_omitted(self):
        rows=[['Euronext Holidays','Paris'],["Friday 1 January 2027 (New Year’s Day)",'Closed'],
              ['Friday 26 March 2027 (Good Friday)','Closed'],['Monday 29 March 2027 (Easter Monday)','Closed'],
              ['Friday 24 December 2027','Half Trading Day'],['Friday 31 December 2027','Half Trading Day']]
        parsed=sync.parse_euronext_paris('Calendar of business days'+table(rows))
        self.assertEqual(len(parsed['2027']['holidays']),3)
        with self.assertRaises(ValueError):sync.parse_euronext_paris('Calendar of business days'+table(rows[:-1]))

    def test_real_source_facts_publish_future_years_atomically_and_hold_conflicts(self):
        raw=runtime.seed_path().read_bytes()
        lse=table(fixture('lse-rolling.json')['rows'])
        xetra=fixture('xetra-annual.json')
        sources={sync.SOURCES['UK_EQ']['urls'][0]:lse,
                 sync.SOURCES['DE_EQ']['urls'][0]:xetra['scope']+table(xetra['rows']),
                 sync.SOURCES['FR_EQ']['urls'][0]:'Calendar of business days'+''.join(table(g) for g in fixture('euronext-paris.json')['tables'])}
        for year in ('2026','2027'):
            sources[sync.SOURCES['TW_EQ']['urls'][0]+'&date='+year+'0101']=json.dumps(fixture('twse-'+year+('-zh.json' if year=='2026' else '-unpublished.json')))
            if year=='2026':data=fixture('twse-2026-en.json')
            else:data={**fixture('twse-2027-unpublished.json'),'fields':['Date','Description'],'title':'Holiday Schedule of 2027'}
            sources[sync.SOURCES['TW_EQ']['urls'][1]+'&date='+year+'0101']=json.dumps(data)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'calendars/releases').mkdir(parents=True)
            revision=runtime.validate(raw)['revision']
            index={'schemaVersion':1,'sha256':revision,'path':'calendars/releases/'+revision+'.json'}
            (root/'calendars/current.json').write_bytes(runtime.canonical(index));(root/index['path']).write_bytes(raw)
            def fetch(url):
                if url not in sources:raise OSError('fixture source not provided')
                return sources[url].encode()
            now=datetime(2026,10,23,9,tzinfo=ZoneInfo('Asia/Shanghai'))
            result=sync.sync(root,now=now,force=True,fetch=fetch)
            self.assertIn('UK_EQ:2027',result['changedCalendars'])
            self.assertIn('DE_EQ:2032',result['changedCalendars'])
            self.assertEqual(result['results']['TW_EQ'],'verified_no_change')
            self.assertEqual(result['results']['FR_EQ'],'verified_no_change')
            new_index=json.loads((root/'calendars/current.json').read_bytes())
            snapshot=runtime.validate((root/new_index['path']).read_bytes())
            self.assertEqual(snapshot['revision'],new_index['sha256'])
            self.assertEqual(runtime.day_rule(snapshot,'DE_EQ','2026-12-30')['kind'],'UNKNOWN')
            self.assertEqual(runtime.day_rule(snapshot,'US_EQ','2026-12-30')['kind'],'NORMAL')
            conflict=fixture('twse-2026-en.json');conflict['data']=[r for r in conflict['data'] if r[0]!='2026-10-26']
            sources[sync.SOURCES['TW_EQ']['urls'][1]+'&date=20260101']=json.dumps(conflict)
            held=sync.sync(root,now=now,force=True,fetch=fetch)
            self.assertEqual(held['results']['TW_EQ'],'held_preserving_active_calendar')
            self.assertEqual(held['activeRevision'],snapshot['revision'])


if __name__=='__main__':unittest.main()
