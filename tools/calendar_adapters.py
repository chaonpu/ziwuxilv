"""Scoped official annual adapters; no source readability implies full coverage."""
from datetime import date, timedelta
import re
from calendar_runtime import require
from calendar_sync import Page, MONTHS, LABELS, blank, parse_china

WEEKDAYS = 'Monday Tuesday Wednesday Thursday Friday Saturday Sunday'.split()
MONTH_PATTERN = '|'.join(MONTHS)


def checked_day(year, month, number, weekday=None):
    result = date(int(year), MONTHS[month], int(number))
    require(weekday is None or result.strftime('%A') == weekday, 'annual_weekday_mismatch')
    return result


def parse_hkex(text, hours):
    require('Half Day Trading' in Page(hours).plain() and '12:08' in hours and '12:10' in hours,
            'hkex_half_day_clock_evidence_missing')
    title = re.search(r'Hong Kong Securities Market Holiday Schedule for Year\s+(20\d{2})', text)
    require(title is not None and 'Securities Market is closed on Saturdays, Sundays' in text,
            'hkex_securities_annual_scope_missing')
    year = title[1]; record = blank(year, [])
    require('The following trading days are half-day trading day' in text, 'hkex_half_days_missing')
    closed, half = text[title.end():].split('The following trading days are half-day trading day', 1)
    pattern = r'(\d{1,2})\s+('+MONTH_PATTERN+r')\s+('+year+r')\s+('+'|'.join(WEEKDAYS)+r')\b'
    for m in re.finditer(pattern, closed):
        d = checked_day(m[3], m[2], m[1], m[4]).isoformat()
        require(d not in record['holidays'], 'hkex_duplicate_holiday')
        record['holidays'][d] = '港股休市'
    pattern = r'(\d{1,2})\s+('+MONTH_PATTERN+r')\s+('+year+r')\s*\(('+'|'.join(WEEKDAYS)+r')\)\s*-'
    for m in re.finditer(pattern, half):
        d = checked_day(m[3], m[2], m[1], m[4]).isoformat()
        require(d not in record['holidays'] and d not in record['overrides'], 'hkex_half_day_conflict')
        record['overrides'][d] = {'halfDay':True, 'closeMinute':730}
    require(10 <= len(record['holidays']) <= 20 and len(record['overrides']) == 3,
            'hkex_annual_incomplete')
    require(f'{year}-01-01' in record['holidays'] and any(d.startswith(year+'-12-') for d in record['holidays']),
            'hkex_annual_boundaries_missing')
    return {year:record}


def parse_b3(html):
    out = {}
    groups = list(re.finditer(r'<h2[^>]*>\s*Market Calendar (20\d{2})\s*</h2>(.*?)(?=<h2[^>]*>\s*Market Calendar|$)', html, re.S))
    latest = max((m[1] for m in groups), default='')
    for m in groups:
        year, block = m.groups(); record = blank(year, [])
        if year != latest:continue
        require('Holiday calendar for '+year in Page(block).plain(), 'b3_annual_scope_missing')
        blocks = re.split(r'<a\b[^>]*>\s*('+MONTH_PATTERN+r')\s*</a>', block)
        months = blocks[1::2]
        if year != latest and months != list(MONTHS):continue
        require(months == list(MONTHS), 'b3_twelve_months_missing')
        for i in range(1, len(blocks), 2):
            for row in Page(blocks[i+1]).rows:
                if not row or not row[0].isdigit():continue
                require(len(row) == 4, 'b3_row_shape_changed')
                d = date(int(year), MONTHS[blocks[i]], int(row[0])).isoformat()
                details = row[3]
                # Foreign clearing/bank holidays do not close Brazilian equities.
                if 'no trading on the equity' in details:
                    require('Listed B3' in details or 'BM&FBOVESPA Segment' in details, 'b3_equity_scope_changed')
                    require(d not in record['holidays'], 'b3_duplicate_equity_holiday')
                    record['holidays'][d] = row[1][:100]
                elif 'Ash Wednesday' in row[1]:
                    require('Trading and registration will start at 1:00 p.m.' in details,
                            'b3_special_open_time_changed')
                    record['overrides'][d] = {'openMinute':780}
                elif 'Listed B3' in details or 'B3 Listed' in details:
                    raise ValueError('b3_unrecognized_equity_event')
        require(10 <= len(record['holidays']) <= 18 and len(record['overrides']) == 1,
                'b3_annual_incomplete')
        require(f'{year}-01-01' in record['holidays'] and f'{year}-12-31' in record['holidays'],
                'b3_year_end_missing')
        out[year] = record
    require(bool(out), 'b3_annual_table_missing')
    return out


def parse_ice_brent(text):
    require('IFEU Trading Schedule: Oil, Utilities' in text and 'TRADING SCHEDULE KEY' in text,
            'ice_oil_product_schedule_missing')
    # -raw extraction preserves reading order; headers are removed, not dates.
    text = re.sub(r'(?m)^IFEU Trading Schedule:.*\n|^© .*\n', '', text)
    sections = re.split(r'(?m)^(20\d{2}) Trading Schedule\s*$', text)
    out = {}
    for i in range(1, len(sections), 2):
        year, block = sections[i], sections[i+1].split('TRADING SCHEDULE KEY')[0]
        record = blank(year, []); seen = set()
        pattern = r'(?m)^('+'|'.join(WEEKDAYS)+r')\s+(\d{1,2})\s+('+MONTH_PATTERN+r')(?:\s+(20\d{2}))?\b'
        matches = list(re.finditer(pattern, block))
        for j, match in enumerate(matches):
            day_year = match[4] or year
            d = checked_day(day_year, match[3], match[2], match[1])
            if str(d.year) != year:continue
            body = block[match.end():matches[j+1].start() if j+1 < len(matches) else len(block)]
            # Status must precede product-specific additional closure text.
            status = re.search(r'(?<!\w)(Closed|TD|A|B|C\*?|D)(?!\w)', body)
            require(status is not None or 'Appendix' in body, 'ice_annual_status_unknown')
            value = 'Appendix' if 'Appendix' in body else status[1]
            label = d.isoformat(); seen.add(label)
            if value == 'Closed':record['holidays'][label] = 'ICE布伦特休市'
            elif value not in ('A', 'TD') or 'Appendix' in body:
                record['unknownDates'].append(label)
        # Year-end appendices can contain additional exceptional days outside
        # the main table. Retain their exact printed dates as UNKNOWN.
        for number, month, explicit_year in re.findall(r'\b(\d{1,2})\s+(December)\s+(20\d{2})\b', text):
            if explicit_year == year:
                d = date(int(year), 12, int(number)).isoformat()
                if d not in record['holidays']:record['unknownDates'].append(d)
        record['unknownDates'] = sorted(set(record['unknownDates']) - set(record['holidays']))
        require(len(seen) >= 14 and f'{year}-01-01' in record['holidays']
                and any(d.startswith(year+'-03-') or d.startswith(year+'-04-') for d in record['holidays'])
                and any(d.startswith(year+'-12-') for d in seen), 'ice_annual_incomplete')
        out[year] = record
    require(bool(out), 'ice_annual_schedule_missing')
    return out


def parse_cme_affected_days(html):
    out = {}
    # Globex only: floor, clearing, BrokerTec and EBS are different calendars.
    groups = re.findall(r'<h[1-6]\b[^>]*>\s*(20\d{2}) CME Globex Trading Schedule\s*</h[1-6]>(.*?)(?=<h[1-6]\b|$)', html, re.S)
    for year, block in groups:
        rows = Page(block).rows
        header = ['U.S. HOLIDAY', 'INCLUDES THE FOLLOWING DATES:', 'HOLIDAY HOURS']
        require(header in rows, 'cme_globex_table_missing')
        rows = rows[rows.index(header)+1:]
        require(len(rows) == 11 and all(len(r) == 3 and r[2] == 'Holiday Schedule' for r in rows),
                'cme_globex_annual_incomplete')
        require('New Year' in rows[0][0] and 'New Year' in rows[-1][0]
                and all(any(label in r[0] for r in rows) for label in ('Thanksgiving', 'Christmas', 'Juneteenth', 'Labor')),
                'cme_globex_holiday_groups_missing')
        record = blank(year, [])
        for row in rows:
            value = row[1].replace('–', '-').replace('—', '-')
            cross = re.fullmatch(r'(\d{1,2}) ('+MONTH_PATTERN+r') (20\d{2})\s*-\s*(\d{1,2}) ('+MONTH_PATTERN+r') (20\d{2})', value)
            same = re.fullmatch(r'(\d{1,2})\s*-\s*(\d{1,2}) ('+MONTH_PATTERN+r') (20\d{2})', value)
            if cross:
                a = checked_day(cross[3], cross[2], cross[1]); b = checked_day(cross[6], cross[5], cross[4])
            elif same:
                a = checked_day(same[4], same[3], same[1]); b = checked_day(same[4], same[3], same[2])
            else:raise ValueError('cme_globex_date_range_changed')
            require(0 <= (b-a).days <= 7, 'cme_globex_date_range_invalid')
            while a <= b:
                if str(a.year) == year:record['unknownDates'].append(a.isoformat())
                a += timedelta(days=1)
        record['unknownDates'] = sorted(set(record['unknownDates']))
        require(20 <= len(record['unknownDates']) <= 60, 'cme_affected_days_incomplete')
        out[year] = record
    require(bool(out), 'cme_globex_annual_scope_missing')
    return out


def parse_china_futures(html):
    text = Page(html).plain()
    # Reuse the checked seven-group range parser, with the futures annual title.
    title = re.search(r'(20\d{2})年(?:部分节假日)?休市安排', text)
    require(title is not None, 'china_futures_annual_title_missing')
    year = title[1]
    adapted = re.sub(r'('+year+r')年休市安排', r'\1年部分节假日休市安排', html, count=1)
    candidates = parse_china(adapted)
    suspended = []
    for group in LABELS:
        part = re.search(re.escape(group)+r'\s*[:：](.*?)(?=(?:一|二|三|四|五|六|七|八)[、.]|$)', text[title.end():], re.S)
        require(part is not None, 'china_futures_group_missing')
        matches = re.findall(r'(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日[^。]*?(?:晚上|当晚)不进行夜盘交易', part[1])
        require(len(matches) == 1, 'china_futures_night_suspension_missing')
        yy, mm, dd = matches[0]
        suspension = date(int(yy or year), int(mm), int(dd)).isoformat()
        suspended.append(suspension)
    require(len(set(suspended)) == 7, 'china_futures_night_suspension_duplicate')
    return candidates, sorted(suspended)


def parse_china_futures_english(html):
    text=Page(html).plain().replace('’',"'")
    title=re.search(r'(?:Notice on )?Trading Schedule during National Holidays for Year\s+(20\d{2})',text,re.I)
    require(title is not None,'czce_english_annual_title_missing')
    year=int(title[1]);record=blank(str(year),[]);nights=[]
    names=("New Year's Day",'Spring Festival','Qingming Festival','Labor Day','Dragon Boat Festival','Mid-Autumn Festival','National Day')
    names_pattern='|'.join(re.escape(n) for n in names)
    parts=re.split(r'(?:\d+\s*[.)]\s*)?('+names_pattern+r')\s*:',text[title.end():],flags=re.I)
    found={parts[i].lower():parts[i+1] for i in range(1,len(parts),2)}
    require(set(found)=={n.lower() for n in names},'czce_seven_groups_missing')
    pattern=r'('+MONTH_PATTERN+r')\s+(\d{1,2}),?\s+(20\d{2})\s*\(('+'|'.join(WEEKDAYS)+r')\)'
    for name,label in zip(names,LABELS):
        part=found[name.lower()]
        period=re.search(r'The market will be closed (?:during holidays )?(?:from|on) (.*?)(?:and will be|The market will)',part,re.I|re.S)
        require(period is not None,'czce_closure_range_missing')
        values=re.findall(pattern,period[1]);require(len(values) in (1,2),'czce_closure_dates_invalid')
        first=checked_day(values[0][2],values[0][0],values[0][1],values[0][3]);last=checked_day(values[-1][2],values[-1][0],values[-1][1],values[-1][3])
        require(first.year==year and last.year==year and 0<=(last-first).days<=16,'czce_closure_range_invalid')
        while first<=last:
            record['holidays'][first.isoformat()]=label;first+=timedelta(days=1)
        night=re.search(r'There will be no night trading session on\s+'+pattern,part,re.I)
        require(night is not None,'czce_night_suspension_missing')
        nights.append(checked_day(night[3],night[1],night[2],night[4]).isoformat())
    require(15<=len(record['holidays'])<=55 and len(set(nights))==7,'czce_annual_incomplete')
    return {str(year):record},sorted(nights)


def reviewed_china_futures(root,spec,venue,url,now):
    """Year-bounded facts reviewed against official notice index text.

    This is an explicit reviewed fallback, never labelled as a live parse or
    given an invented digest for an unavailable source document.
    """
    import json,hashlib
    from calendar_runtime import canonical
    path=spec.get('reviewedFallback','')
    match=re.fullmatch(r'calendars/reviewed/([0-9a-f]{64})\.json',path)
    require(match is not None,'china_reviewed_fallback_not_configured')
    raw=(root/path).read_bytes()
    require(hashlib.sha256(raw).hexdigest()==match[1],'china_reviewed_fallback_digest_mismatch')
    obj=json.loads(raw);year=str(obj.get('year',''))
    require(obj.get('schemaVersion')==1 and obj.get('calendarId')=='CN_FUTURES'
            and re.fullmatch('20[0-9]{2}',year) and obj.get('verificationMode')=='reviewed_official_index',
            'china_reviewed_fallback_scope_invalid')
    require(date.fromisoformat(obj['reviewedAt'])<=now.date()<=date.fromisoformat(obj['validThrough'])
            and obj['validThrough']==year+'-12-31' and int(year)>=now.year,'china_reviewed_fallback_expired')
    item=obj['venues'].get(venue,{})
    require(item.get('sourceUrl')==url and item.get('noticeYear')==int(year),'china_reviewed_venue_source_mismatch')
    ranges=item.get('holidayRanges',{})
    require(set(ranges)==set(LABELS),'china_reviewed_seven_groups_required')
    record=blank(year,[url])
    for label,(first,last) in ranges.items():
        first=date.fromisoformat(first);last=date.fromisoformat(last)
        require(first.year==int(year) and last.year==int(year) and 0<=(last-first).days<=16,'china_reviewed_range_invalid')
        while first<=last:
            require(first.isoformat() not in record['holidays'],'china_reviewed_holiday_overlap')
            record['holidays'][first.isoformat()]=label;first+=timedelta(days=1)
    night=item.get('nightSuspensionDates',[])
    require(len(night)==(0 if venue=='GFEX' else 7) and len(set(night))==len(night)
            and all(int(year)-1<=date.fromisoformat(d).year<=int(year) for d in night),'china_reviewed_night_dates_invalid')
    require(15<=len(record['holidays'])<=55,'china_reviewed_annual_incomplete')
    return {year:record},sorted(night),{'mode':'reviewed_official_index','reviewedAt':obj['reviewedAt'],
        'reviewManifestSha256':match[1],'sourceUrl':url}


def a50_unknown_provider_calendar(cal,html,first_unknown):
    """Withdraw unsupported future day claims; never infer a CFD session."""
    from copy import deepcopy
    require('富时中国A50' in html and 'CFD' in html
            and re.search(r'futures_app\.code\s*=\s*["\']CHA50CFD["\']',html), 'a50_provider_instrument_identity_missing')
    boundary=date.fromisoformat(first_unknown);result={};count=0
    for year,old in cal['years'].items():
        if int(year)<boundary.year:continue
        record=deepcopy(old);cursor=max(boundary,date(int(year),1,1));end=date(int(year),12,31)
        unknown=set(record['unknownDates'])
        while cursor<=end:
            label=cursor.isoformat();unknown.add(label)
            record['holidays'].pop(label,None);record['overrides'].pop(label,None)
            cursor+=timedelta(days=1);count+=1
        record['unknownDates']=sorted(unknown);result[year]=record
    require(0<count<=1100,'a50_uncertainty_range_invalid')
    return result


def parse_saudi(html, minimum_year=None):
    page = Page(html)
    require('Saudi Exchange Holiday Calendar' in page.plain(), 'saudi_exchange_scope_missing')
    out = {}; groups = {}
    for row in page.rows:
        if len(row) != 2:continue
        matches = re.findall(r'\b(\d{1,2})/(\d{1,2})/(20\d{2})\b', row[0])
        if not matches:continue
        title = row[1]
        if 'Founding Day' in title:label = '建国日'
        elif 'National Day' in title:label = '国庆日'
        elif 'Eid Al Adha' in title:label = '宰牲节'
        elif re.search(r'Eid Al Fit[e]?r', title):label = '开斋节'
        else:continue  # Listing announcements are not holidays.
        first = date(int(matches[0][2]), int(matches[0][1]), int(matches[0][0]))
        last = date(int(matches[-1][2]), int(matches[-1][1]), int(matches[-1][0]))
        if first.year < (minimum_year or date.today().year):continue
        require(first.year == last.year and 0 <= (last-first).days <= 14, 'saudi_holiday_range_invalid')
        year = str(first.year); record = out.setdefault(year, blank(year, [])); groups.setdefault(year, set()).add(label)
        if label in ('开斋节', '宰牲节'):
            # Listed range endpoints have varied historically. A dated final
            # trading/resumption statement is mandatory; estimates stay UNKNOWN.
            resume = re.search(r'Trading will resume after the holiday on\s*(\d{1,2})/(\d{1,2})/(20\d{2})', title)
            stopped = re.search(r'end of trading day\s*(\d{1,2})/(\d{1,2})/(20\d{2})', title)
            require(resume is not None and stopped is not None, 'saudi_final_trading_dates_missing')
            stop = date(int(stopped[3]), int(stopped[2]), int(stopped[1]))
            end = date(int(resume[3]), int(resume[2]), int(resume[1]))
            require(stop < first <= last < end and 0 < (end-stop).days <= 15, 'saudi_resumption_conflict')
            first, last = stop+timedelta(days=1), end-timedelta(days=1)
        while first <= last:
            if first.isoweekday() in (7,1,2,3,4):
                if 'UMM AL-QURA' in title:record['unknownDates'].append(first.isoformat())
                else:record['holidays'][first.isoformat()] = label
            first += timedelta(days=1)
    complete = {y:r for y,r in out.items() if groups[y] == {'建国日', '国庆日', '开斋节', '宰牲节'}}
    require(bool(complete), 'saudi_four_annual_holiday_groups_missing')
    return complete


def parse_bse(html):
    from bisect import bisect_right
    page=Page(html);text=''.join(page.text);offsets=[0]
    for chunk in page.text:offsets.append(offsets[-1]+len(chunk))
    headings=list(re.finditer(r'Trading\s+Holidays\s+for(?:\s+the)?(?:\s+Calendar\s+Year)?\s+(20\d{2})',text,re.I))
    scopes=[]
    for i,heading in enumerate(headings):
        label=text[heading.end():headings[i+1].start() if i+1<len(headings) else len(text)][:500]
        if re.match(r'\s*[-–—]?\s*Equity\s+Segment\s*,?\s*Equity\s+Derivatives?\s+Segment',label,re.I):
            scopes.append((heading.start(),heading[1],True))
        elif re.match(r'\s*[-–—]?\s*(?:for\s+(?:the\s+)?)?(?:Currency|Commodity|Debt|Electronic|SLB|RFQ|NDS)',label,re.I):
            scopes.append((heading.start(),heading[1],False))
    positions=[scope[0] for scope in scopes];rows_by_year={};notes_by_year={}
    for i,(start,year,equity) in enumerate(scopes):
        if equity:notes_by_year.setdefault(year,[]).append(text[start:scopes[i+1][0] if i+1<len(scopes) else len(text)])
    for row,index in zip(page.rows,page.row_text_indices):
        scope=bisect_right(positions,offsets[index])-1
        if scope>=0 and scopes[scope][2]:rows_by_year.setdefault(scopes[scope][1],[]).append(row)
    out={}
    for year,rows in rows_by_year.items():
        record = blank(year, []); ordinals = []
        for row in rows:
            if len(row) != 4 or not row[0].isdigit():continue
            found = re.fullmatch(r'(\d{2})-([A-Za-z]{3})-(\d{2})', row[2])
            require(found is not None and '20'+found[3] == year, 'bse_equity_row_date_invalid')
            months = {name[:3].lower():number for name, number in MONTHS.items()}
            day = date(int(year), months[found[2].lower()], int(found[1]))
            require(day.strftime('%A') == row[3], 'bse_equity_weekday_mismatch')
            if day.isoweekday()>5:continue
            require(day.isoformat() not in record['holidays'], 'bse_duplicate_holiday')
            ordinals.append(int(row[0])); record['holidays'][day.isoformat()] = row[1]
        require(ordinals == list(range(1, len(ordinals)+1)) and 13 <= len(ordinals) <= 22,
                'bse_equity_annual_incomplete')
        require(any('Republic' in v for v in record['holidays'].values())
                and any('Christmas' in v for v in record['holidays'].values()), 'bse_annual_boundaries_missing')
        # A generic holiday table is insufficient evidence for special Sunday
        # opening times (Muhurat/Budget sessions). Keep known special dates UNKNOWN.
        for sentence in re.findall(r'Muhurat[^.\n]{0,250}',' '.join(' '.join(notes_by_year.get(year,[])).split()),re.I):
            values=[]
            for m in re.finditer(r'(\d{1,2})(?:st|nd|rd|th)?[ -]+('+MONTH_PATTERN+r')[ , -]+('+year+r')',sentence,re.I):
                values.append(date(int(year),MONTHS[m[2].title()],int(m[1])).isoformat())
            for m in re.finditer(r'('+MONTH_PATTERN+r')[ , -]+(\d{1,2})(?:st|nd|rd|th)?[ , -]+('+year+r')',sentence,re.I):
                values.append(date(int(year),MONTHS[m[1].title()],int(m[2])).isoformat())
            for special in values:
                record['holidays'].pop(special,None)
                record['unknownDates'].append(special)
        record['unknownDates']=sorted(set(record['unknownDates']))
        out[year] = record
    require(bool(out), 'bse_equity_annual_scope_missing')
    return out


def parse_bse_master(payload):
    import json
    from html import escape
    obj=json.loads(payload)
    require(obj.get('format')=='bse_equity_master_tables_v1','bse_master_normalized_scope_invalid')
    text=' '.join(' '.join(obj['texts']).split());rows=[];years=set()
    for table in obj['tables']:
        for row in table:
            row=[v for v in row if v is not None and str(v).strip()]
            if len(row)!=4 or not str(row[0]).strip().isdigit():continue
            row=[' '.join(str(v or '').split()) for v in row]
            row[2]=re.sub(r',\s*',', ',row[2])
            day=None
            for pattern in ('%d-%b-%y','%d-%b-%Y','%B %d, %Y','%d %B, %Y','%d %B %Y','%B %d %Y'):
                try:
                    from datetime import datetime
                    day=datetime.strptime(row[2],pattern).date();break
                except ValueError:pass
            require(day is not None,'bse_master_row_date_invalid')
            require(day.strftime('%A')==row[3],'bse_equity_weekday_mismatch')
            if day.isoweekday()>5:continue # Separate weekend-holiday table.
            years.add(str(day.year));rows.append([str(len(rows)+1),row[1],day.strftime('%d-%b-%y'),row[3]])
    require(len(years)==1,'bse_master_annual_year_ambiguous:'+','.join(sorted(years)));year=next(iter(years))
    require(year in text and 'Trading Holidays' in text,'bse_master_annual_heading_missing')
    html='Trading Holidays for '+year+' - Equity Segment, Equity Derivative Segment<table>'
    notes=' '.join(re.findall(r'Muhurat[^.\n]{0,250}',text,re.I))
    html+=''.join('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</table><p>'+escape(notes)+'</p>'
    return parse_bse(html)


def parse_sgx(payload):
    import json, calendar
    obj=json.loads(payload);year=obj.get('year')
    require(obj.get('format')=='sgx_vector_calendar_v1' and obj.get('contract')=='NK'
            and isinstance(year,int) and 2020<=year<=2099, 'sgx_calendar_normalized_scope_invalid')
    require([m['month'] for m in obj['months']]==list(range(1,13)), 'sgx_twelve_months_missing')
    record=blank(str(year),[]);last=expiry=0
    for month in obj['months']:
        require(month['days']==calendar.monthrange(year,month['month'])[1], 'sgx_month_days_invalid')
        for event in month['events']:
            d=date(year,month['month'],event['day']).isoformat()
            require(event['kind'] in ('closed','lastTrading','expiry') and isinstance(event['allExcept'],bool)
                    and event['codes'] and all(re.fullmatch('[A-Z][A-Z0-9]*',c) for c in event['codes']), 'sgx_event_invalid')
            affected=('NK' not in event['codes']) if event['allExcept'] else ('NK' in event['codes'])
            if not affected:continue
            if event['kind']=='closed':record['holidays'][d]='SGX日经期货休市'
            elif event['kind']=='lastTrading':last+=1
            else:expiry+=1
    require(f'{year}-01-01' in record['holidays'] and len(record['holidays'])<=20
            and last==12 and expiry==12, 'sgx_nikkei_annual_incomplete')
    return {str(year):record}


def parse_krx(payload, year):
    import json
    obj = json.loads(payload)
    require(obj.get('year') == int(year) and obj.get('market') == 'KRX_CASH', 'krx_query_identity_mismatch')
    data = obj.get('response', {})
    require(isinstance(data, dict) and set(data) == {'block1'} and isinstance(data['block1'], list),
            'krx_annual_response_invalid')
    if not data['block1']:return {}
    record = blank(str(year), [])
    for row in data['block1']:
        require(isinstance(row, dict) and {'calnd_dd', 'calnd_dd_dy', 'kr_dy_tp', 'dy_tp_cd', 'holdy_eng_nm'} <= row.keys(),
                'krx_annual_fields_changed')
        day = date.fromisoformat(row['calnd_dd'])
        require(str(day.year) == str(year) and row['calnd_dd_dy'] == day.isoformat()
                and day.strftime('%A') == row['kr_dy_tp'] and day.strftime('%a').upper() == row['dy_tp_cd']
                and day.isoweekday() <= 5, 'krx_annual_date_invalid')
        require(day.isoformat() not in record['holidays'], 'krx_annual_duplicate_date')
        record['holidays'][day.isoformat()] = row['holdy_eng_nm'].strip() or '韩股休市'
    require(10 <= len(record['holidays']) <= 30
            and any(d.startswith(str(year)+'-12-') and label == 'End of Year Holiday'
                    for d,label in record['holidays'].items()), 'krx_annual_incomplete')
    first = date(int(year), 1, 1)
    require(first.isoweekday() > 5 or first.isoformat() in record['holidays'], 'krx_new_year_missing')
    return {str(year):record}


def guarded_source(adapter, texts):
    """Explicitly describe unsupported scope instead of claiming verification."""
    if adapter == 'blocked-a50-provider':
        raise ValueError('a50_cfd_provider_calendar_required_sgx_futures_not_equivalent')
    if adapter == 'blocked-sgx-product':
        require(any('Nikkei' in text or 'sgx' in text.lower() for text in texts), 'sgx_product_source_missing')
        raise ValueError('sgx_nikkei_product_closure_symbols_not_yet_machine_verified')
    raise ValueError('unknown_guarded_calendar_adapter')
