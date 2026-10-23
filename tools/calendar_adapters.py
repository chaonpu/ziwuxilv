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
        matches = re.findall(r'(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日[^。]*?晚上不进行夜盘交易', part[1])
        require(len(matches) == 1, 'china_futures_night_suspension_missing')
        yy, mm, dd = matches[0]
        suspension = date(int(yy or year), int(mm), int(dd)).isoformat()
        suspended.append(suspension)
    require(len(set(suspended)) == 7, 'china_futures_night_suspension_duplicate')
    return candidates, sorted(suspended)


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
    out = {}
    sections = re.split(r'Trading Holidays for\s+(20\d{2})\s*-\s*Equity Segment,?\s*Equity Derivative Segment', html)
    for i in range(1, len(sections), 2):
        year, block = sections[i], sections[i+1]
        block = re.split(r'Trading Holidays for|The holidays falling on Saturday|Holidays falling on Saturday', block)[0]
        record = blank(year, []); ordinals = []
        for row in Page(block).rows:
            if len(row) != 4 or not row[0].isdigit():continue
            found = re.fullmatch(r'(\d{2})-([A-Za-z]{3})-(\d{2})', row[2])
            require(found is not None and '20'+found[3] == year, 'bse_equity_row_date_invalid')
            months = {name[:3].lower():number for name, number in MONTHS.items()}
            day = date(int(year), months[found[2].lower()], int(found[1]))
            require(day.strftime('%A') == row[3], 'bse_equity_weekday_mismatch')
            require(day.isoformat() not in record['holidays'], 'bse_duplicate_holiday')
            ordinals.append(int(row[0])); record['holidays'][day.isoformat()] = row[1]
        require(ordinals == list(range(1, len(ordinals)+1)) and 13 <= len(ordinals) <= 22,
                'bse_equity_annual_incomplete')
        require(any('Republic' in v for v in record['holidays'].values())
                and any('Christmas' in v for v in record['holidays'].values()), 'bse_annual_boundaries_missing')
        # A generic holiday table is insufficient evidence for special Sunday
        # opening times (Muhurat/Budget sessions). Keep known special dates UNKNOWN.
        for m in re.finditer(r'Muhurat[^.\n]*?(\d{1,2})[ -]('+MONTH_PATTERN+r')[ , -]+('+year+r')', Page(html).plain(), re.I):
            special = date(int(year), MONTHS[m[2].title()], int(m[1])).isoformat()
            record['holidays'].pop(special, None)
            record['unknownDates'].append(special)
        out[year] = record
    require(bool(out), 'bse_equity_annual_scope_missing')
    return out


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
