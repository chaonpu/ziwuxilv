"""Bounded third-party CFD reference calendar, never a Sina provider guarantee.

The quote identity stays CHA50CFD. FOREX.com's own CFD specification and live
holiday table supply a clearly labelled reference window; no SGX substitution.
"""
from copy import deepcopy
from datetime import date, timedelta
import re
import unicodedata
from calendar_runtime import require


def reference_schedule(identity, holiday_html, contracts, today):
    from calendar_sync import Page
    require('富时中国A50' in identity and 'CFD' in identity
            and re.search(r'futures_app\.code\s*=\s*["\']CHA50CFD["\']',identity),
            'a50_sina_cfd_identity_missing')
    text=unicodedata.normalize('NFKC',contracts)
    rows=re.findall(r'(?:CHINAA50|CHINA\s+A50(?:\s+CFD\s+Micro|\s+Index))\s*\([^)]*A50[^)]*\)\s*([^\n]+)',text)
    pattern=r'09:00\s*-\s*16:30\s*&\s*17:00\s*-\s*05:15\s*周一至周五\s*\(新加坡\)'
    require(2<=len(rows)<=6 and all(re.match(pattern,row) for row in rows),'a50_cfd_weekly_specification_changed')
    plain=Page(holiday_html).plain()
    require('交易时间和节假日安排' in plain and '即将到来的节假日' in plain
            and re.search(r'北京时间\s*\(GMT\+8\)',unicodedata.normalize('NFKC',plain))
            and re.search(r'©\s*FOREX\.COM\s*'+str(today.year),plain,re.I),
            'a50_reference_page_scope_or_year_invalid')
    tables=[]
    for html in re.findall(r'<table\b[^>]*>.*?</table>',holiday_html,re.I|re.S):
        rows=Page(html).rows
        selected=[row for row in rows if row and row[0]=='中国A50指数']
        if not selected:continue
        require(len(selected)==1 and rows and rows[0][0]=='产品名称','a50_reference_product_table_ambiguous')
        header=rows[0][1:];values=selected[0][1:]
        require(4<=len(header)<=40 and len(header)==len(values),'a50_reference_table_incomplete')
        days=[]
        for cell in header:
            found=re.fullmatch(r'(\d{1,2})月(\d{1,2})日',cell)
            require(found is not None,'a50_reference_date_format_changed')
            options=[]
            for year in (today.year-1,today.year,today.year+1):
                try:day=date(year,int(found[1]),int(found[2]))
                except ValueError:continue
                if -45<=(day-today).days<=62:options.append(day)
            require(len(options)==1,'a50_reference_date_outside_rolling_window')
            days.append(options[0])
        require(days==sorted(set(days)) and 7<=(days[-1]-days[0]).days<=62
                and (days[0]-today).days<=31 and days[-1]>today,'a50_reference_stale_or_disordered_table')
        result={}
        for day,value in zip(days,values):
            if value in ('照常','正常交易','Normal trading hours'):kind='NORMAL'
            elif value in ('休市','闭市','Closed'):kind='CLOSED'
            else:kind='UNKNOWN' # Split/partial sessions need product clocks.
            result[day.isoformat()]=kind
        tables.append(result)
    require(tables and all(item==tables[0] for item in tables),'a50_reference_tables_missing_or_conflicting')
    end=min(date.fromisoformat(max(tables[0])),today+timedelta(days=45))
    return {'start':(today+timedelta(days=1)).isoformat(),'end':end.isoformat(),'exceptions':tables[0],
            'provider':'FOREX.com','evidenceLevel':'third_party_reference',
            'quoteInstrument':'SINA:CHA50CFD','referenceInstrument':'FOREX.com:CHINAA50 CFD',
            'providerScheduleVerified':False,'regularWeekdays':[1,2,3,4,5],
            'regularSessions':['09:00-16:30','17:00-05:15+1'],'timezone':'Asia/Shanghai',
            'dateResolution':'upcoming_month_day_relative_to_check','maximumHorizonDays':45}


def apply_reference(calendar, schedule, today):
    """Only explicit daily reference overrides resolve future UNKNOWN entries."""
    from calendar_sync import blank
    start=date.fromisoformat(schedule['start']);end=date.fromisoformat(schedule['end'])
    require(today<start<=end and (end-today).days<=45,'a50_reference_application_window_invalid')
    years={str(y) for y in range(start.year,end.year+1)}
    years.update(y for y,r in calendar['years'].items() if r.get('reference',{}).get('evidenceLevel')=='third_party_reference' and int(y)>=today.year)
    result={}
    for year in sorted(years):
        record=deepcopy(calendar['years'].get(year) or blank(year,[]))
        unknown=set(record['unknownDates'])
        previous=record.get('reference',{})
        if previous.get('evidenceLevel')=='third_party_reference':
            cursor=max(today+timedelta(days=1),date.fromisoformat(previous['start']))
            last=date.fromisoformat(previous['end'])
            while cursor<=last:
                if str(cursor.year)==year:
                    day=cursor.isoformat();record['overrides'].pop(day,None);record['holidays'].pop(day,None);unknown.add(day)
                cursor+=timedelta(days=1)
        # New records must not make the rest of an unverified year NORMAL.
        if year not in calendar['years']:
            cursor=date(int(year),1,1)
            while cursor.year==int(year):unknown.add(cursor.isoformat());cursor+=timedelta(days=1)
        cursor=start
        while cursor<=end:
            if str(cursor.year)==year:
                day=cursor.isoformat();kind=schedule['exceptions'].get(day,'NORMAL')
                record['overrides'].pop(day,None);record['holidays'].pop(day,None);unknown.discard(day)
                if kind=='UNKNOWN':unknown.add(day)
                elif kind=='CLOSED':record['holidays'][day]='A50参考休市'
                elif cursor.isoweekday()>5:record['holidays'][day]='周末（A50参考日历）'
                else:record['overrides'][day]={} # Explicit verified reference day.
            cursor+=timedelta(days=1)
        record['unknownDates']=sorted(unknown)
        record['reference']={k:v for k,v in schedule.items() if k!='exceptions'}
        result[year]=record
    return result
