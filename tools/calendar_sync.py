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
import subprocess
import http.cookiejar
import urllib.request
from urllib.parse import urlparse, urljoin, parse_qs, urlencode
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


def parse_china_english(html):
    text=Page(html).plain()
    title=re.search(r'Stock Market Holiday Schedule\s*\(\s*(20\s*\d\s*\d)\s*\)',text)
    require(title is not None,'szse_english_annual_title_missing')
    year=int(re.sub(r'\s','',title[1]));out=blank(year,[])
    names=('New Year','Spring Festival','Qingming Festival','Labour Day','Dragon Boat Festival','Mid-Autumn Festival','National Day')
    for name,label in zip(names,LABELS):
        found=re.search(re.escape(name)+r'\s*[:：](.*?)resume trading',text[title.end():],re.S)
        require(found is not None,'szse_english_holiday_missing:'+name)
        values=re.findall(r'('+'|'.join(MONTHS)+r')\s*(\d{1,2})(?:st|nd|rd|th)?',found[1])
        require(len(values) in (1,2),'szse_english_range_ambiguous')
        start=date(year,MONTHS[values[0][0]],int(values[0][1]));end=date(year,MONTHS[values[-1][0]],int(values[-1][1]))
        require(0<=(end-start).days<=16,'szse_english_range_invalid')
        while start<=end:out['holidays'][start.isoformat()]=label;start+=timedelta(days=1)
    require(15<=len(out['holidays'])<=55,'szse_english_annual_count_invalid')
    return {str(year):out}


def parse_jpx(html):
    out={}
    short={name[:3]:number for name,number in MONTHS.items()}
    for year,block in re.findall(r'<h2\b[^>]*>\s*(?:<[^>]+>\s*)*(20\d{2})(?:\s*</[^>]+>)*\s*</h2>(.*?)(?=<h2\b|$)',html,re.S):
        record=blank(year,[])
        for row in Page(block).rows:
            if len(row)!=2:continue
            found=re.fullmatch(r'([A-Z][a-z]{2})\.?\s+(\d{1,2})\s*\([A-Za-z]+\.?\)',row[0])
            require(found is not None,'jpx_holiday_row_format_changed')
            day=date(int(year),short[found[1]],int(found[2]))
            if day.isoweekday()<=5:record['holidays'][day.isoformat()]='休市'
        require(15<=len(record['holidays'])<=25,'jpx_annual_count_invalid')
        out[year]=record
    require(bool(out),'jpx_annual_tables_missing')
    return out


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
        if not any(month in row[0] for month in MONTHS):continue
        value=english_day(row[0],year)
        if not value:continue
        if 'Closed' in row[2]:out['holidays'][value]='休市'
        elif '1:00' in row[2]:out['overrides'][value]={'closeMinute':780,'postCloseMinute':1020}
    require(9<=len(out['holidays'])<=10 and 1<=len(out['overrides'])<=3,'nasdaq_annual_counts_invalid')
    return {year:out}


def parse_twse(payload, *, english=False):
    obj=json.loads(payload)
    if obj.get('stat')=='很抱歉，沒有符合條件的資料!':return {}
    require(obj.get('stat')=='ok' and isinstance(obj.get('data'),list),'twse_response_invalid')
    year=str(obj.get('queryYear',''))
    require(bool(re.fullmatch(r'20\d{2}',year)) and obj.get('date')==year+'0101','twse_year_invalid')
    title=obj.get('title','')
    require((title=='Holiday Schedule of '+year) if english else bool(re.fullmatch(str(int(year)-1911)+r'\s*年市場開休市日期',title)), 'twse_annual_title_invalid')
    require(obj.get('fields')==(['Date','Description'] if english else ['日期','名稱','說明']),'twse_fields_changed')
    if not obj['data'] and obj.get('total')==0:return {}
    out=blank(year,[]);seen=set()
    for row in obj['data']:
        require(isinstance(row,list) and len(row)==(2 if english else 3),'twse_row_invalid')
        day=date.fromisoformat(row[0]);name=row[1]
        require(str(day.year)==year and day.isoformat() not in seen and isinstance(name,str) and bool(name),'twse_row_date_invalid')
        seen.add(day.isoformat())
        if not english and ('開始交易日' in name or '最後交易日' in name):continue
        if not english:require('放假' in row[2] or '補假' in row[2] or '市場無交易' in name,'twse_unknown_event')
        if day.isoweekday()<=5:out['holidays'][day.isoformat()]=name
    require(12<=len(out['holidays'])<=30,'twse_annual_incomplete')
    return {year:out}


def parse_lse(html):
    out={};names={}
    for row in Page(html).rows:
        if len(row)!=4 or not re.search(r'20\d{2}',row[0]):continue
        found=re.fullmatch(r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) (\d{1,2}) ('+'|'.join(MONTHS)+r') (20\d{2})',row[0])
        require(found is not None,'lse_date_format_changed')
        weekday,number,month,year=found.groups();day=date(int(year),MONTHS[month],int(number))
        require(day.strftime('%A')==weekday,'lse_date_weekday_mismatch')
        record=out.setdefault(year,blank(year,[]));labels=names.setdefault(year,[])
        require(day.isoformat() not in record['holidays'] and day.isoformat() not in record['overrides'],'lse_duplicate_date')
        if row[2].startswith('NON-trading day.'):
            record['holidays'][day.isoformat()]=row[1];labels.append(row[1])
        elif row[2].startswith('Markets closing process commences from 12:30 London time.'):
            require('half day' in row[1],'lse_special_session_changed')
            record['overrides'][day.isoformat()]={'closeMinute':750}
        else:raise ValueError('lse_unknown_market_status')
    expected=('New Year','Good Friday','Easter Monday','Early May','Spring Bank','Summer Bank','Christmas Day','Boxing Day')
    # The LSE table is rolling. Partial first/last years are never full coverage.
    complete={y:r for y,r in out.items() if len(r['holidays'])==8 and len(r['overrides'])==2
              and all(sum(n in label for label in names[y])==1 for n in expected)}
    require(bool(complete),'lse_complete_annual_schedule_missing')
    return complete


def parse_xetra(html):
    page=Page(html);text=page.plain()
    require('Non-trading days at Frankfurter' in text and 'No trading but settlement is open' in text,'xetra_scope_missing')
    rows=page.rows;header=next((r for r in rows if r and not r[0] and len(r)>1 and all(re.fullmatch(r'20\d{2}',c) for c in r[1:])),None)
    require(header is not None and len(set(header[1:]))==len(header)-1,'xetra_year_header_invalid')
    out={y:blank(y,[]) for y in header[1:]}
    labels=("New Year's Day",'Good Friday','Easter Monday','Labor Day','Christmas Eve**','Christmas Day','Boxing Day',"New Year's Eve**")
    selected=rows[rows.index(header)+1:rows.index(header)+9]
    require(len(selected)==8,'xetra_annual_rows_missing')
    months={name[:3]:m for name,m in MONTHS.items()}
    for row,label in zip(selected,labels):
        require(len(row)==len(header) and row[0]==label,'xetra_holiday_table_changed')
        for year,cell in zip(header[1:],row[1:]):
            found=re.fullmatch(r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) ([A-Z][a-z]{2}) (\d{2}), '+year,cell)
            require(found is not None,'xetra_date_format_changed')
            weekday,month,number=found.groups();day=date(int(year),months[month],int(number))
            require(day.strftime('%A')==weekday,'xetra_date_weekday_mismatch')
            out[year]['holidays'][day.isoformat()]=label
    # The annual page explicitly leaves the final session's clocks to a later
    # circular. Keep that one session UNKNOWN; never infer normal closing time.
    for year,record in out.items():
        cursor=date(int(year),12,31)
        while cursor.isoweekday()>5 or cursor.isoformat() in record['holidays']:cursor-=timedelta(days=1)
        record['unknownDates']=[cursor.isoformat()]
    return out


def parse_euronext_paris(html):
    require('Calendar of business days' in Page(html).plain(),'euronext_annual_title_missing')
    groups=[Page(table).rows for table in re.findall(r'<table\b[^>]*>.*?</table>',html,re.S)]
    groups=[g for g in groups if g and g[0] and g[0][0]=='Euronext Holidays']
    out={}
    for group in groups:
        header=group[0]
        require(header.count('Paris')==1,'euronext_paris_column_missing')
        column=header.index('Paris');years={y for r in group[1:] for y in re.findall(r'20\d{2}',r[0])}
        require(len(years)==1,'euronext_annual_year_ambiguous')
        year=years.pop();record=blank(year,[]);explicit=set();closed_labels=[]
        for row in group[1:]:
            require(len(row)==len(header),'euronext_row_shape_changed')
            status=row[column].strip('* ')
            found=re.fullmatch(r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) (\d{1,2}) ('+'|'.join(MONTHS)+r')(?: '+year+r')?(?: \(.*\))?',row[0])
            if found:explicit.add(date(int(year),MONTHS[found[2]],int(found[1])).isoformat())
            if status in ('Full Trading Day','Full Day Trading'):continue
            require(status in ('Closed','Half Trading Day','Half Day Trading'),'euronext_paris_status_unknown')
            require(found is not None,'euronext_single_date_missing')
            day=date(int(year),MONTHS[found[2]],int(found[1])).isoformat()
            require(day not in record['holidays'] and day not in record['unknownDates'],'euronext_duplicate_date')
            if status=='Closed':record['holidays'][day]='休市';closed_labels.append(row[0])
            else:record['unknownDates'].append(day)
        require(2<=len(record['holidays'])<=8 and 0<=len(record['unknownDates'])<=3
                and all(any(label in row for row in closed_labels) for label in ('Good Friday','Easter Monday')),'euronext_annual_incomplete')
        # Weekends can leave Paris with only three full weekday closures (2027).
        # Verify the expected annual sections instead of imposing a five-day
        # count, and require explicit year-end rows so truncation cannot silently
        # turn an omitted half-day into a normal full trading session.
        for month,number in ((1,1),(5,1),(12,25),(12,26)):
            d=date(int(year),month,number)
            require(d.isoweekday()>5 or d.isoformat() in record['holidays'],'euronext_fixed_holiday_section_missing')
        for number in (24,31):
            d=date(int(year),12,number)
            require(d.isoweekday()>5 or d.isoformat() in explicit,'euronext_year_end_section_missing')
        out[year]=record
    require(bool(out),'euronext_annual_table_missing')
    return out


def should_check(cal, health, now, force=False, *, adapter=None):
    next_year=str(now.year+1)
    missing=next_year not in cal['years']
    daily=now.month>=10 and missing and adapter!='source-monitor' and not (adapter or '').startswith('blocked-')
    hours=24 if daily else 168
    previous=health.get('lastAttemptAt')
    if not previous:return True,'q4_missing_next_year' if daily else 'weekly'
    try:
        prior=datetime.fromisoformat(previous)
        require(prior.tzinfo is not None,'ambiguous_check_timestamp')
        prior=prior.astimezone(now.tzinfo)
        age=(now-prior).total_seconds()
        require(age>=-300,'future_check_timestamp')
    except (TypeError,ValueError):return True,'invalid_check_timestamp'
    # The weekly cron runs on Sunday, not the ISO Monday week boundary.
    week_start=lambda value:value.date()-timedelta(days=value.isoweekday()%7)
    period_changed=(now.date()>prior.date()) if daily else week_start(now)>week_start(prior)
    return force or period_changed or age>=hours*3600, 'manual' if force else 'q4_missing_next_year' if daily else 'weekly'


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        before, after = urlparse(req.full_url), urlparse(newurl)
        require(after.scheme == 'https' and after.hostname == before.hostname
                and after.port in (None, 443) and not after.username, 'calendar_source_redirect_domain_changed')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_krx(year):
    require(re.fullmatch(r'20\d{2}', str(year)), 'krx_query_year_invalid')
    base = 'https://global.krx.co.kr'
    path = '/contents/GLB/05/0501/0501110000/GLB0501110000.jsp'
    opener = urllib.request.build_opener(SameHostRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    headers = {'User-Agent':'Mozilla/5.0 ZiWuXiLv-Calendar/1.0', 'Referer':base+path,
               'X-Requested-With':'XMLHttpRequest'}
    def read(url, body=None, maximum=2_000_000):
        request = urllib.request.Request(url, headers=headers,
            data=None if body is None else urlencode(body).encode())
        with opener.open(request, timeout=15) as response:raw = response.read(maximum+1)
        require(0 < len(raw) <= maximum, 'krx_response_size_invalid')
        return raw
    page = read(base+path).decode('utf-8')
    require('Market Closing(Holiday)' in page and 'glb0501110000_01' in page, 'krx_public_form_changed')
    query = urlencode({'name':'form', 'bld':'GLB/05/0501/0501110000/glb0501110000_01'})
    code = read(base+'/contents/COM/GenerateOTP.jspx?'+query, maximum=8192).decode().strip()
    require(10 < len(code) <= 8192 and '<' not in code, 'krx_public_query_code_invalid')
    raw = read(base+'/contents/GLB/99/GLB99000001.jspx',
               {'search_bas_yy':str(year), 'gridTp':'KRX', 'code':code, 'pagePath':path})
    return canonical({'year':int(year), 'market':'KRX_CASH', 'response':json.loads(raw)})


def source_text(raw, url=None):
    if url and urlparse(url).hostname=='api2.sgx.com' and re.search(r'/SGX%20Calendar%20(20\d{2})(?:_[0-9]+)?\.pdf$',url):
        from calendar_pdf import sgx_facts
        year=int(re.search(r'SGX%20Calendar%20(20\d{2})',url)[1])
        return canonical(sgx_facts(raw,year)).decode()
    if raw.startswith(b'%PDF'):
        result = subprocess.run(['pdftotext','-raw','-','-'], input=raw, capture_output=True, timeout=15)
        require(result.returncode == 0 and 0 < len(result.stdout) <= 2_000_000, 'calendar_pdf_text_invalid')
        return result.stdout.decode('utf-8-sig')
    charset=re.search(br'charset\s*=\s*["\']?\s*(gb2312|gbk|gb18030)',raw[:8192],re.I)
    return raw.decode('gb18030' if charset else 'utf-8-sig')


def source_get(url):
    allowed={url for spec in SOURCES.values() for url in spec['urls']+spec.get('discoveryUrls',[])}
    discovered_szse=(urlparse(url).scheme=='https' and urlparse(url).hostname=='www.szse.cn'
                     and bool(re.fullmatch(r'/disclosure/notice/general/t[0-9]{8}_[0-9]+\.html',urlparse(url).path)))
    year_query=any(spec.get('yearQuery') and any(re.fullmatch(re.escape(base)+
        (r'\?search_bas_yy=20\d{2}' if spec.get('yearParameter') else r'&date=20\d{2}0101'),url) for base in spec['urls']) for spec in SOURCES.values())
    discovered_document=any(re.fullmatch(pattern,url) for spec in SOURCES.values() for pattern in spec.get('documentPatterns',[]))
    require(url in allowed or discovered_szse or year_query or discovered_document,'unregistered_calendar_source')
    if urlparse(url).hostname == 'global.krx.co.kr' and year_query:
        return fetch_krx(parse_qs(urlparse(url).query)['search_bas_yy'][0])
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 ZiWuXiLv-Calendar/1.0','Accept':'text/html,application/pdf,application/json'})
    with urllib.request.build_opener(SameHostRedirect()).open(req,timeout=15) as response:
        final=response.geturl()
        require(urlparse(final).scheme=='https' and urlparse(final).hostname==urlparse(url).hostname,'calendar_source_redirect_domain_changed')
        maximum=6_000_000 if urlparse(url).hostname=='api2.sgx.com' and '/SGX%20Calendar%20' in url else 2_000_000
        raw=response.read(maximum+1)
    require(0<len(raw)<=maximum,'calendar_source_size_invalid')
    return raw


SOURCES=json.loads((ROOT/'calendars/sources.json').read_bytes())['calendars']


def stable_record(record):
    return {k:v for k,v in record.items() if k not in ('sources','evidenceHash')}


def accept(old, candidate, *, allow_unknown=False):
    require(allow_unknown or not candidate['unknownDates'],'candidate_contains_unverified_sessions')
    if old:
        # Calendar name wording is not a session change. Existing dates may be
        # added only in the future; deletion/retroactive change is held.
        require(set(old['holidays'])<=set(candidate['holidays']),'candidate_removes_holiday')
        require(set(old['overrides'])<=set(candidate['overrides']),'candidate_removes_special_session')
        for day,clocks in old['overrides'].items():
            require(candidate['overrides'][day]==clocks,'candidate_changes_existing_session')
        require(set(old['unknownDates'])<=set(candidate['unknownDates'])|set(candidate['holidays'])|set(candidate['overrides']),'candidate_silently_resolves_unknown_session')
    return candidate


def sync(root=ROOT, *, now=None, force=False, fetch=source_get):
    now=now or datetime.now(ZoneInfo('Asia/Shanghai'))
    index=json.loads((root/'calendars/current.json').read_bytes())
    raw=(root/index['path']).read_bytes();current=validate(raw)
    require(current['revision']==index['sha256'],'current_calendar_digest_mismatch')
    bundle=deepcopy(current['data'])
    hp=root/'calendars/health.json'
    health=json.loads(hp.read_text()) if hp.exists() else {'schemaVersion':1,'calendars':{}}
    active={}; discoveries={}
    for key,cal in bundle['calendars'].items():
        state=health['calendars'].get(key,{})
        spec=SOURCES.get(key,{'adapter':'source-monitor','urls':list(dict.fromkeys(s for r in cal['years'].values() for s in r['sources']))})
        due,reason=should_check(cal,state,now,force,adapter=spec['adapter'] if spec.get('publishEnabled',True) else 'source-monitor')
        if not due:continue
        if spec.get('yearQuery'):
            suffix = (lambda y:'?search_bas_yy='+str(y)) if spec.get('yearParameter') else (lambda y:'&date='+str(y)+'0101')
            spec={**spec,'urls':[url+suffix(year) for year in (now.year,now.year+1) for url in spec['urls']]}
        for listing in spec.get('discoveryUrls', []):
            try:
                raw_listing = fetch(listing); html = source_text(raw_listing)
                found = []
                for href, label in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S|re.I):
                    document = urljoin(listing, href)
                    if 'holiday' in Page(label).plain().lower() and any(re.fullmatch(p,document) for p in spec.get('documentPatterns', [])):
                        found.append(document)
                require(len(set(found)) <= 6, 'calendar_discovery_limit_exceeded')
                spec = {**spec, 'urls':list(dict.fromkeys(spec['urls']+found))}
                discoveries[key] = {'status':'ok', 'documents':len(set(found)), 'sha256':hashlib.sha256(raw_listing).hexdigest()}
            except Exception as exc:discoveries[key] = {'status':'unavailable', 'reason':type(exc).__name__}
        active[key]=(spec,reason)
    urls=sorted({url for spec,_ in active.values() for url in spec['urls']})
    def fetch_one(url):
        try:
            body=fetch(url)
            return url,{'status':'ok','sha256':hashlib.sha256(body).hexdigest(),'text':source_text(body,url)}
        except Exception as exc:
            return url,{'status':'unavailable','reason':type(exc).__name__}
    with ThreadPoolExecutor(max_workers=4) as pool:results=dict(pool.map(fetch_one,urls))
    changed=[];checked=[]
    for key,(spec,reason) in active.items():
        records=[results[u] for u in spec['urls']]
        original_years=deepcopy(bundle['calendars'][key]['years'])
        state={'lastAttemptAt':now.isoformat(timespec='seconds'),'cadence':reason,
               'adapter':spec['adapter'],'sourceResults':[{k:v for k,v in r.items() if k!='text'}|{'url':u} for u,r in zip(spec['urls'],records)]}
        if spec.get('capability'):state['capability']=spec['capability']
        if key in discoveries:state['discovery']=discoveries[key]
        # A failed attempt must not erase the last successful source/verification
        # timestamps. They are distinct from this attempt and from content age.
        previous=health['calendars'].get(key,{})
        for field in ('lastSourceSuccessAt','lastVerifiedAt'):
            if previous.get(field):state[field]=previous[field]
        try:
            if spec['adapter'].startswith('blocked-'):
                from calendar_adapters import guarded_source
                guarded_source(spec['adapter'], [r.get('text','') for r in records])
            if spec['adapter']!='china-futures-consensus':
                require(all(r['status']=='ok' for r in records),'official_source_unavailable')
            if spec['adapter']=='china-pair':
                first=parse_china(records[0]['text'])
                year=next(iter(first))
                second=parse_china_english(records[1]['text'])
                weekdays=lambda values:{d for d in values if date.fromisoformat(d).isoweekday()<=5}
                require(first.keys()==second.keys() and weekdays(first[year]['holidays'])==weekdays(second[year]['holidays']),'china_official_sources_conflict')
                candidates=first
            elif spec['adapter']=='us-pair':
                first=parse_nyse(records[0]['text']);second=parse_nasdaq(records[1]['text'])
                candidates={}
                for year in first.keys() & second.keys():
                    require(set(first[year]['holidays'])==set(second[year]['holidays'])
                            and first[year]['overrides']==second[year]['overrides'],'us_official_sources_conflict')
                    candidates[year]=first[year]
                require(bool(candidates),'us_matching_annual_sources_missing')
            elif spec['adapter']=='jpx-annual':
                candidates=parse_jpx(records[0]['text'])
            elif spec['adapter']=='twse-pair':
                candidates={};unpublished=[]
                for i in range(0,len(records),2):
                    year=re.search(r'&date=(20\d{2})0101$',spec['urls'][i])[1]
                    first=parse_twse(records[i]['text']);second=parse_twse(records[i+1]['text'],english=True)
                    require(first.keys()==second.keys(),'twse_official_year_conflict')
                    if not first:unpublished.append(year);continue
                    require(set(first)=={year} and set(first[year]['holidays'])==set(second[year]['holidays']),'twse_official_sources_conflict')
                    candidates[year]=first[year]
                require(bool(candidates),'twse_no_published_years')
                state['unpublishedYears']=unpublished
            elif spec['adapter']=='lse-annual':candidates=parse_lse(records[0]['text'])
            elif spec['adapter']=='xetra-annual':candidates=parse_xetra(records[0]['text'])
            elif spec['adapter']=='euronext-paris':candidates=parse_euronext_paris(records[0]['text'])
            elif spec['adapter']=='hkex-securities-pdf':
                from calendar_adapters import parse_hkex
                hours=next(r['text'] for u,r in zip(spec['urls'],records) if 'Securities-Market?' in u)
                candidates={}
                for u,r in zip(spec['urls'],records):
                    if '.pdf' not in u:continue
                    parsed=parse_hkex(r['text'],hours)
                    for year,new in parsed.items():
                        require(year not in candidates or stable_record(candidates[year])==stable_record(new),'hkex_annual_sources_conflict')
                        candidates[year]=new
            elif spec['adapter']=='b3-equities-annual':
                from calendar_adapters import parse_b3
                candidates=parse_b3(records[0]['text'])
            elif spec['adapter']=='ice-brent-pdf':
                from calendar_adapters import parse_ice_brent
                candidates=parse_ice_brent(records[0]['text'])
            elif spec['adapter']=='cme-globex-affected-days':
                from calendar_adapters import parse_cme_affected_days
                candidates=parse_cme_affected_days(records[0]['text'])
            elif spec['adapter']=='krx-public-form':
                from calendar_adapters import parse_krx
                candidates={};unpublished=[]
                for u,r in zip(spec['urls'],records):
                    year=parse_qs(urlparse(u).query)['search_bas_yy'][0]
                    parsed=parse_krx(r['text'],year)
                    if not parsed:unpublished.append(year)
                    candidates.update(parsed)
                state['unpublishedYears']=unpublished
                require(bool(candidates),'krx_published_annual_data_missing')
            elif spec['adapter']=='china-futures-consensus':
                from calendar_adapters import parse_china_futures,parse_china_futures_english
                parsed=[];state['venueValidation']={}
                for venue,u,r in zip(('SHFE','INE','DCE','CZCE','GFEX'),spec['urls'],records):
                    try:
                        require(r['status']=='ok','official_source_unavailable')
                        if venue=='GFEX':data,night=parse_china(r['text']),[]
                        elif venue=='CZCE' and 'english.czce.com.cn' in u:data,night=parse_china_futures_english(r['text'])
                        else:data,night=parse_china_futures(r['text'])
                        state['venueValidation'][venue]={'status':'parsed','years':sorted(data),'nightSuspensionDates':night}
                        evidence={'schemaVersion':1,'venue':venue,'sourceUrl':u,'sourceSha256':r['sha256'],
                                  'calendars':data,'nightSuspensionDates':night}
                        evidence_raw=canonical(evidence);digest=hashlib.sha256(evidence_raw).hexdigest()
                        path=root/'calendars/evidence/CN_FUTURES'/venue/(digest+'.json')
                        path.parent.mkdir(parents=True,exist_ok=True)
                        if not path.exists():path.write_bytes(evidence_raw)
                        state['venueValidation'][venue]['evidenceSha256']=digest
                        parsed.append((data,night))
                    except (ValueError,KeyError,TypeError) as exc:
                        state['venueValidation'][venue]={'status':'held','reason':str(exc)[:150]}
                require(len(parsed)==5,'china_five_venue_complete_sources_required')
                fifth=parsed.pop()[0]
                candidates=parsed[0][0]
                require(all(p[0].keys()==candidates.keys() and all(set(p[0][y]['holidays'])==set(candidates[y]['holidays']) for y in candidates) for p in parsed)
                        and fifth.keys()==candidates.keys() and all(set(fifth[y]['holidays'])==set(candidates[y]['holidays']) for y in candidates), 'china_five_venue_holidays_conflict')
                require(all(p[1]==parsed[0][1] for p in parsed), 'china_futures_night_rules_conflict')
                state['nightSuspensionDates']=parsed[0][1]
            elif spec['adapter']=='saudi-exchange-annual':
                from calendar_adapters import parse_saudi
                candidates=parse_saudi(records[0]['text'],now.year)
            elif spec['adapter']=='bse-equities-annual':
                from calendar_adapters import parse_bse
                candidates=parse_bse(records[0]['text'])
            elif spec['adapter']=='sgx-nikkei-vector':
                from calendar_adapters import parse_sgx
                candidates=parse_sgx(records[0]['text'])
            else:
                state['status']='source_checked_format_requires_adapter'
                candidates={}
            held={}
            for year,new in candidates.items():
                if int(year)<now.year:continue
                new['sources']=spec['urls']
                old=bundle['calendars'][key]['years'].get(year)
                try:
                    require(spec.get('publishEnabled',True), spec.get('blockReason','first_live_validation_required'))
                    accept(old,new,allow_unknown=spec.get('capability')=='annual_partial_sessions' or spec['adapter'] in ('xetra-annual','euronext-paris'))
                    if old and set(old['holidays'])==set(new['holidays']) and old['overrides']==new['overrides'] and set(old['unknownDates'])==set(new['unknownDates']):continue
                    if old:
                        additions=(set(new['holidays'])-set(old['holidays']))|(set(new['overrides'])-set(old['overrides']))|(set(new['unknownDates'])-set(old['unknownDates']))
                        require(all(d>=now.date().isoformat() for d in additions),'candidate_changes_past_dates')
                except ValueError as exc:
                    held[year]=str(exc)
                    proposal=deepcopy(current['data']);proposal['calendars'][key]['years'][year]=new
                    proposal_raw=canonical(proposal);proposal_snapshot=validate(proposal_raw)
                    path=root/'calendars/candidates'/ (proposal_snapshot['revision']+'.json')
                    path.parent.mkdir(parents=True,exist_ok=True)
                    if path.exists():require(path.read_bytes()==proposal_raw,'immutable_candidate_conflict')
                    else:path.write_bytes(proposal_raw)
                    from calendar_ops import impact
                    assessment=impact(current,proposal_snapshot,now.date())
                    (path.with_suffix('.impact.json')).write_bytes(canonical(assessment))
                    state.setdefault('candidateRevisions',{})[year]=proposal_snapshot['revision']
                    continue
                bundle['calendars'][key]['years'][year]=new;changed.append(key+':'+year)
            state['parsedYears']=sorted(candidates)
            if held:
                state['heldYears']=held
                state['status']='published_with_held_years' if any(c.startswith(key+':') for c in changed) else 'held_preserving_active_calendar'
                state['reason']='; '.join(y+':'+r for y,r in held.items())[:200]
            state.setdefault('status','verified_no_change' if not any(c.startswith(key+':') for c in changed) else 'published')
            state['lastSourceSuccessAt']=now.isoformat(timespec='seconds')
            if candidates:state['lastVerifiedAt']=now.isoformat(timespec='seconds')
        except (ValueError,KeyError,TypeError,UnicodeError,OSError) as exc:
            bundle['calendars'][key]['years']=original_years
            changed=[c for c in changed if not c.startswith(key+':')]
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
    warnings=[]
    for key,cal in bundle['calendars'].items():
        year=now.year
        if str(year) not in cal['years']:
            warnings.append({'calendarId':key,'level':'critical','reason':'current_year_not_covered'})
            continue
        while str(year+1) in cal['years']:year+=1
        remaining=(date(year,12,31)-now.date()).days
        if remaining<=90:
            warnings.append({'calendarId':key,'level':'critical' if remaining<=7 else 'warning',
                             'reason':'next_year_not_covered','daysRemaining':remaining})
    health['coverageWarnings']=warnings
    if checked:hp.write_bytes(canonical(health))
    if checked and root==ROOT:
        from calendar_ops import record, status
        record(root,'check',revision,{'sourceChecks':len(urls),'changedCalendars':changed},now=now)
        status(root,now=now)
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
