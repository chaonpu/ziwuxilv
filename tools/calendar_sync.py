"""Official-source calendar maintenance: weekly, Q4 daily for missing years.

Only supported, complete official formats may change active calendars. Other
venues are monitored for source changes, never interpreted as empty holidays.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import urllib.request
from urllib.parse import urlparse, urljoin
from zoneinfo import ZoneInfo
from calendar_runtime import canonical, validate, require

ROOT = Path(__file__).resolve().parents[1]
MONTHS = {name:i for i,name in enumerate(('January','February','March','April','May','June','July','August','September','October','November','December'),1)}
LABELS = ('元旦','春节','清明节','劳动节','端午节','中秋节','国庆节')


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.rows=[]; self.row=None; self.cell=None; self.text=[]; self.hidden=0
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'):self.hidden+=1
        if tag=='tr':self.row=[]
        if tag in ('td','th') and self.row is not None:self.cell=[]
        if tag in ('p','br','tr','div','li','h1','h2','h3','h4'):self.text.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
        if tag in ('td','th') and self.cell is not None:
            self.row.append(' '.join(' '.join(self.cell).split()));self.cell=None
        if tag=='tr' and self.row is not None:
            self.rows.append(self.row);self.row=None
    def handle_data(self, value):
        if not self.hidden:
            self.text.append(value)
            if self.cell is not None:self.cell.append(value)
    def plain(self):return re.sub(r'[ \t\r\f\v]+',' ',''.join(self.text))


def blank(year, sources):
    return dict(coverageStart=f'{year}-01-01',coverageEnd=f'{year}-12-31',verified=True,
                sources=sources,holidays={},unknownDates=[],overrides={})


def parse_china(html):
    text=Page(html).plain()
    title=re.search(r'(20\d{2})年(?:部分节假日)?休市安排',text)
    require(title is not None,'china_annual_title_missing')
    year=int(title[1]); out=blank(year,[])
    # Both exchange pages explicitly name all seven annual holiday groups.
    for label in LABELS:
        part=re.search(re.escape(label)+r'\s*[:：](.*?)休市',text[title.end():],re.S)
        require(part is not None,'china_annual_holiday_missing:'+label)
        values=re.findall(r'(\d{1,2})月(\d{1,2})日',part[1])
        require(len(values) in (1,2),'china_holiday_range_ambiguous')
        start=date(year,*map(int,values[0]));end=date(year,*map(int,values[-1]))
        require(0<=(end-start).days<=16,'china_holiday_range_invalid')
        while start<=end:
            require(start.isoformat() not in out['holidays'],'china_holiday_overlap')
            out['holidays'][start.isoformat()]=label;start+=timedelta(days=1)
    require(15<=len(out['holidays'])<=55,'china_annual_count_invalid')
    return {str(year):out}


def english_day(cell, year):
    found=re.search(r'('+'|'.join(MONTHS)+r')\s+(\d{1,2})\b',cell)
    if not found:
        require(bool(re.fullmatch(r'[—–\-\s*]*',cell)),'us_holiday_date_missing')
        return None
    return date(int(year),MONTHS[found[1]],int(found[2])).isoformat()


def parse_nyse(html):
    page=Page(html); rows=page.rows
    header=next((r for r in rows if r and r[0].lower()=='holiday' and len(r)>=3),None)
    require(header is not None,'nyse_annual_table_missing')
    years=[re.search(r'20\d{2}',c)[0] for c in header[1:]]
    require(1<=len(years)<=5 and len(set(years))==len(years),'nyse_year_header_invalid')
    out={y:blank(y,[]) for y in years}; names=['元旦','马丁·路德·金纪念日','华盛顿诞辰日','耶稣受难日','阵亡将士纪念日','六月节','独立日','劳动节','感恩节','圣诞节']
    start=rows.index(header)+1; holiday_rows=rows[start:start+10]
    require(len(holiday_rows)==10 and all(len(r)==len(header) for r in holiday_rows),'nyse_annual_rows_incomplete')
    expected=['New Year','Martin Luther','Washington','Good Friday','Memorial','Juneteenth','Independence','Labor','Thanksgiving','Christmas']
    for row,label,name in zip(holiday_rows,expected,names):
        require(label in row[0],'nyse_holiday_order_changed')
        for year,cell in zip(years,row[1:]):
            value=english_day(cell,year)
            if value:out[year]['holidays'][value]=name
    text=page.plain()
    require('close early' in text and '1:00 p.m.' in text,'nyse_early_close_section_missing')
    sections=re.findall(r'will close early at 1:00 p\.m\.(.*?)(?:All times are Eastern Time|\n\s*\n)',text,re.S)
    require(bool(sections),'nyse_early_close_format_changed')
    for section in sections:
        for month,day,year in re.findall(r'('+'|'.join(MONTHS)+r')\s+(\d{1,2}),\s*(20\d{2})',section):
            if year in out:
                value=date(int(year),MONTHS[month],int(day)).isoformat()
                out[year]['overrides'][value]={'closeMinute':780,'postCloseMinute':1020}
    require(all(9<=len(r['holidays'])<=10 and 1<=len(r['overrides'])<=3 for r in out.values()),'nyse_annual_counts_invalid')
    return out


def parse_nasdaq(html):
    page=Page(html);text=page.plain()
    title=re.search(r'Holiday Schedule\s*(20\d{2})',text)
    require(title is not None,'nasdaq_annual_title_missing')
    year=title[1];out=blank(year,[])
    for row in page.rows:
        if len(row)<3:continue
        if not re.search(r'\b'+year+r'\b',row[0]):continue
        value=english_day(row[0],year)
        if not value:continue
        if 'Closed' in row[2]:out['holidays'][value]='休市'
        elif '1:00' in row[2]:out['overrides'][value]={'closeMinute':780,'postCloseMinute':1020}
    require(9<=len(out['holidays'])<=10 and 1<=len(out['overrides'])<=3,'nasdaq_annual_counts_invalid')
    return {year:out}


def should_check(cal, health, now, force=False):
    next_year=str(now.year+1)
    missing=next_year not in cal['years']
    daily=now.month>=10 and missing
    hours=24 if daily else 168
    previous=health.get('lastAttemptAt')
    if not previous:return True,'q4_missing_next_year' if daily else 'weekly'
    try:age=(now-datetime.fromisoformat(previous)).total_seconds()
    except (TypeError,ValueError):return True,'invalid_check_timestamp'
    return force or age>=hours*3600, 'manual' if force else 'q4_missing_next_year' if daily else 'weekly'


def source_get(url):
    allowed={url for spec in SOURCES.values() for url in spec['urls']}
    discovered_szse=(urlparse(url).scheme=='https' and urlparse(url).hostname=='www.szse.cn'
                     and bool(re.fullmatch(r'/disclosure/notice/general/t[0-9]{8}_[0-9]+\.html',urlparse(url).path)))
    require(url in allowed or discovered_szse,'unregistered_calendar_source')
    req=urllib.request.Request(url,headers={'User-Agent':'ziwuxilv-public-calendar/1.0','Accept':'text/html'})
    with urllib.request.urlopen(req,timeout=15) as response:
        final=response.geturl()
        require(urlparse(final).scheme=='https' and urlparse(final).hostname==urlparse(url).hostname,'calendar_source_redirect_domain_changed')
        raw=response.read(2_000_001)
    require(0<len(raw)<=2_000_000,'calendar_source_size_invalid')
    return raw


SOURCES={key:{'adapter':'source-monitor','urls':list(dict.fromkeys(s for r in cal['years'].values() for s in r['sources']))}
         for key,cal in json.loads((ROOT/'tools/calendar-seed.json').read_bytes())['calendars'].items()}
SOURCES.update({
 'CN_EQ':{'adapter':'china-pair','urls':['https://www.sse.com.cn/disclosure/dealinstruc/closed/','https://www.szse.cn/disclosure/notice/general/index.html']},
 'US_EQ':{'adapter':'us-pair','urls':['https://www.nyse.com/trade/hours-calendars','https://www.nasdaqtrader.com/Trader.aspx?id=Calendar']},
})


def stable_record(record):
    return {k:v for k,v in record.items() if k not in ('sources','evidenceHash')}


def accept(old, candidate):
    require(not candidate['unknownDates'],'candidate_contains_unverified_sessions')
    if old:
        # Calendar name wording is not a session change. Existing dates may be
        # added only in the future; deletion/retroactive change is held.
        require(set(old['holidays'])<=set(candidate['holidays']),'candidate_removes_holiday')
        require(set(old['overrides'])<=set(candidate['overrides']),'candidate_removes_special_session')
        for day,clocks in old['overrides'].items():
            require(candidate['overrides'][day]==clocks,'candidate_changes_existing_session')
    return candidate


def sync(root=ROOT, *, now=None, force=False, fetch=source_get):
    now=now or datetime.now(ZoneInfo('Asia/Shanghai'))
    index=json.loads((root/'calendars/current.json').read_bytes())
    raw=(root/index['path']).read_bytes();current=validate(raw)
    require(current['revision']==index['sha256'],'current_calendar_digest_mismatch')
    bundle=deepcopy(current['data'])
    hp=root/'calendars/health.json'
    health=json.loads(hp.read_text()) if hp.exists() else {'schemaVersion':1,'calendars':{}}
    active={}
    for key,cal in bundle['calendars'].items():
        state=health['calendars'].get(key,{})
        due,reason=should_check(cal,state,now,force)
        if not due:continue
        spec=SOURCES.get(key,{'adapter':'source-monitor','urls':list(dict.fromkeys(s for r in cal['years'].values() for s in r['sources']))})
        active[key]=(spec,reason)
    urls=sorted({url for spec,_ in active.values() for url in spec['urls']})
    def fetch_one(url):
        try:
            body=fetch(url)
            return url,{'status':'ok','sha256':hashlib.sha256(body).hexdigest(),'text':body.decode('utf-8-sig')}
        except Exception as exc:
            return url,{'status':'unavailable','reason':type(exc).__name__}
    with ThreadPoolExecutor(max_workers=4) as pool:results=dict(pool.map(fetch_one,urls))
    changed=[];checked=[]
    for key,(spec,reason) in active.items():
        records=[results[u] for u in spec['urls']]
        state={'lastAttemptAt':now.isoformat(timespec='seconds'),'cadence':reason,
               'adapter':spec['adapter'],'sourceResults':[{k:v for k,v in r.items() if k!='text'}|{'url':u} for u,r in zip(spec['urls'],records)]}
        try:
            require(all(r['status']=='ok' for r in records),'official_source_unavailable')
            if spec['adapter']=='china-pair':
                # The SZSE index is for discovery only. Follow a visible annual
                # notice link of the same verified year, never infer from its title.
                first=parse_china(records[0]['text'])
                year=next(iter(first))
                links=re.findall(r'href=[\"\']([^\"\']+)[\"\'][^>]*>([^<]*(?:<[^>]+>[^<]*)*)',records[1]['text'])
                target=next((href for href,label in links if year+'年部分节假日休市安排' in re.sub('<[^>]+>','',label)),None)
                require(target is not None,'szse_annual_notice_not_visible')
                url=urljoin(spec['urls'][1],target)
                require(urlparse(url).hostname=='www.szse.cn','szse_notice_domain_invalid')
                # A discovered official same-domain annual announcement is an
                # explicitly bounded extension of the fixed source allowlist.
                body=fetch(url)
                require(len(body)<=2_000_000,'szse_notice_oversized')
                second=parse_china(body.decode('utf-8-sig'))
                require(first.keys()==second.keys() and first[year]['holidays']==second[year]['holidays'],'china_official_sources_conflict')
                candidates=first
            elif spec['adapter']=='us-pair':
                first=parse_nyse(records[0]['text']);second=parse_nasdaq(records[1]['text'])
                candidates={}
                for year in first.keys() & second.keys():
                    require(set(first[year]['holidays'])==set(second[year]['holidays'])
                            and first[year]['overrides']==second[year]['overrides'],'us_official_sources_conflict')
                    candidates[year]=first[year]
                require(bool(candidates),'us_matching_annual_sources_missing')
            else:
                state['status']='source_checked_format_requires_adapter'
                candidates={}
            for year,new in candidates.items():
                new['sources']=spec['urls']
                old=bundle['calendars'][key]['years'].get(year)
                accept(old,new)
                if old and set(old['holidays'])==set(new['holidays']) and old['overrides']==new['overrides']:continue
                if old:
                    additions=(set(new['holidays'])-set(old['holidays']))|(set(new['overrides'])-set(old['overrides']))
                    require(all(d>=now.date().isoformat() for d in additions),'candidate_changes_past_dates')
                bundle['calendars'][key]['years'][year]=new;changed.append(key+':'+year)
            state.setdefault('status','verified_no_change' if not any(c.startswith(key+':') for c in changed) else 'published')
            state['lastSourceSuccessAt']=now.isoformat(timespec='seconds')
        except (ValueError,KeyError,TypeError,UnicodeError,OSError) as exc:
            state['status']='held_preserving_active_calendar';state['reason']=str(exc)[:200]
        health['calendars'][key]=state;checked.append(key)
    new_raw=canonical(bundle);snapshot=validate(new_raw)
    # The seed is canonical; checks alone never change the active revision.
    revision=snapshot['revision']
    if changed:
        path='calendars/releases/'+revision+'.json'
        target=root/path
        if target.exists():require(target.read_bytes()==new_raw,'immutable_calendar_conflict')
        else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(new_raw)
        (root/'calendars/current.json').write_bytes(canonical({'schemaVersion':1,'sha256':revision,'path':path}))
    else:revision=current['revision']
    health['activeRevision']=revision
    if checked:hp.write_bytes(canonical(health))
    return {'activeRevision':revision,'checkedCalendars':checked,'changedCalendars':changed,
            'sourceChecks':len(urls),'results':{k:health['calendars'][k]['status'] for k in checked}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--force',action='store_true');args=parser.parse_args()
    result=sync(force=args.force)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    if __import__('os').environ.get('GITHUB_STEP_SUMMARY'):
        with open(__import__('os').environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as stream:
            stream.write('### 交易日历检查\n\n版本：'+result['activeRevision']+'\n\n')
            for key,status in result['results'].items():stream.write('- '+key+'：'+status+'\n')
            stream.write('\n本次官方来源请求数：'+str(result['sourceChecks'])+'；日历变更数：'+str(len(result['changedCalendars']))+'。\n')


if __name__=='__main__':main()
