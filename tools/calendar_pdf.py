"""Strict SGX calendar vector extraction; document layout changes fail closed."""
import calendar
from datetime import date
import re
from calendar_runtime import require


def mark_kind(shape):
    items = shape['items']
    if len(items) == 1 and items[0][0] == 're':return 'expiry'
    if len(items) == 3 and all(i[0] == 'l' for i in items):return 'lastTrading'
    if len(items) == 2 and all(i[0] == 'l' for i in items):
        slopes = [(i[2].x-i[1].x)*(i[2].y-i[1].y) for i in items]
        if slopes[0]*slopes[1] < 0:return 'closed'
    raise ValueError('sgx_calendar_symbol_changed')


def sgx_facts(raw, year):
    import fitz
    require(raw.startswith(b'%PDF') and 0 < len(raw) <= 6_000_000, 'sgx_pdf_invalid')
    require(2020 <= year <= 2099, 'sgx_calendar_year_invalid')
    with fitz.open(stream=raw, filetype='pdf') as document:
        require(14 <= len(document) <= 24, 'sgx_calendar_page_count_changed')
        tail = ' '.join(p.get_text() for p in list(document)[14:])
        require(re.search(r'\bNK\s+SGX Nikkei 225 Index Futures\b', tail), 'sgx_nikkei_contract_scope_missing')
        require('December '+str(year-1) in document[2].get_text()
                and 'January '+str(year+1) in document[13].get_text(), 'sgx_calendar_year_binding_missing')
        months = []
        for month in range(1,13):
            page = document[month+1]
            require(abs(page.rect.width-722.835) < .1 and abs(page.rect.height-609.449) < .1,
                    'sgx_calendar_page_geometry_changed')
            spans = [s for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l.get('spans',[])]
            require(calendar.month_name[month].upper() in page.get_text()
                    and any(s['text']==f'{month:02d}' and s['size']>70 for s in spans), 'sgx_calendar_month_missing')
            drawings=page.get_drawings()
            legends = [d for d in drawings if d['type']=='s' and 590<d['rect'].y0<600 and d['rect'].x0<175]
            require([mark_kind(d) for d in legends] == ['lastTrading','expiry','closed'], 'sgx_calendar_legend_changed')
            require(all(t in page.get_text() for t in ('LAST TRADING DAY','EFFECTIVE EXPIRATION DAY','CLOSED')),
                    'sgx_calendar_legend_labels_missing')
            colors = [d['color'] for d in legends]
            require(all(c is not None and all(abs(a-b)<.01 for a,b in zip(c,colors[0])) for c in colors),
                    'sgx_calendar_legend_color_changed')
            days = {}; count = calendar.monthrange(year,month)[1]
            for span in spans:
                if span['font']!='GloberBook' or abs(span['size']-16)>.1 or not span['text'].isdigit():continue
                day = int(span['text']); box = span['bbox']
                col = round((box[2]-102.0266)/99.7795); row = round((box[1]-119.3725)/93.5433)
                require(1<=day<=count and 0<=col<=6 and 0<=row<=4
                        and (date(year,month,day).weekday()+1)%7==col, 'sgx_calendar_day_geometry_invalid')
                require((col,row) not in days and day not in days.values(), 'sgx_calendar_day_duplicate')
                days[col,row] = day
            require(set(days.values()) == set(range(1,count+1)), 'sgx_calendar_month_days_incomplete')
            events=[];markers=[]
            for shape in drawings:
                box=shape['rect'];color=shape['color']
                if shape['type']!='s' or color is None or not all(abs(a-b)<.01 for a,b in zip(color,colors[0])):continue
                if not (115<box.y0<585 and 3<box.width<4.5 and 3<box.height<4.5):continue
                markers.append(shape)
            for shape in markers:
                box=shape['rect']
                kind=mark_kind(shape)
                col=int((box.x0-13.9297)/99.7795);row=int((box.y0-116.4)/93.5433)
                require((col,row) in days, 'sgx_calendar_symbol_without_day')
                following=[d['rect'].y0 for d in markers if d['rect'].y0>box.y0+.5
                           and int((d['rect'].x0-13.9297)/99.7795)==col
                           and int((d['rect'].y0-116.4)/93.5433)==row]
                end=min(following)-3 if following else 116.4+(row+1)*93.5433-1
                lines=sorted([s for s in spans if s['font']=='GloberBook' and 4.99<=s['size']<=5.51
                              and box.x1-.5<=s['bbox'][0]<13.9297+(col+1)*99.7795
                              and box.y0-3<=s['bbox'][1]<end],key=lambda s:(s['bbox'][1],s['bbox'][0]))
                require(lines and abs(lines[0]['bbox'][1]-box.y0)<3, 'sgx_calendar_product_label_missing')
                groups=[]
                for span in lines:
                    if groups and abs(groups[-1][0]['bbox'][1]-span['bbox'][1])<.8:groups[-1].append(span)
                    else:groups.append([span])
                require(all(4.5<=b[0]['bbox'][1]-a[0]['bbox'][1]<=6.5 for a,b in zip(groups,groups[1:])),
                        'sgx_calendar_product_line_gap')
                text=' '.join(' '.join(s['text'].split()) for group in groups for s in sorted(group,key=lambda s:s['bbox'][0]))
                require(bool(text), f'sgx_calendar_product_codes_missing:{month}:{days[col,row]}:{kind}')
                all_except=text.startswith('All contracts closed except ')
                codes=re.findall(r'\b[A-Z][A-Z0-9]*\b',text)
                require(bool(codes) and not re.search(r'[a-z]',text.removeprefix('All contracts closed except ')),
                        'sgx_calendar_product_text_changed')
                events.append({'day':days[col,row],'kind':kind,'allExcept':all_except,'codes':codes})
            require(10<=len(events)<=90, 'sgx_calendar_month_events_incomplete')
            months.append({'month':month,'days':count,'events':events})
        return {'format':'sgx_vector_calendar_v1','year':year,'contract':'NK','months':months}


def bse_equity_tables(raw):
    """Read only section 2.2 of the official Equity Segment Master Circular."""
    import fitz
    require(raw.startswith(b'%PDF') and len(raw)<=6_000_000,'bse_equity_pdf_invalid')
    with fitz.open(stream=raw,filetype='pdf') as doc:
        require(2<=len(doc)<=120,'bse_equity_pdf_page_count_invalid')
        lead=' '.join(doc[i].get_text() for i in range(min(4,len(doc))))
        require('Equity' in lead and 'Master' in lead and 'Circular' in lead,'bse_equity_master_scope_missing')
        active=False;tables=[];texts=[]
        for page in doc:
            text=page.get_text();start=re.search(r'2\.2\.?\s+Trading Holidays',text,re.I)
            end=re.search(r'2\.3\.?\s+',text,re.I)
            if start:active=True
            if not active:continue
            top=0;bottom=page.rect.height
            if start:
                hits=page.search_for('Trading Holidays')
                require(hits,'bse_equity_section_heading_changed');top=hits[-1].y0
            if end:
                hits=page.search_for('2.3')
                require(hits,'bse_equity_section_end_changed');bottom=hits[-1].y0
            if bottom>top:
                clip=fitz.Rect(0,top,page.rect.width,bottom)
                texts.append(page.get_text(clip=clip))
                for table in page.find_tables(clip=clip).tables:tables.append(table.extract())
            if end:active=False
        require(tables,'bse_equity_annual_pdf_tables_missing')
        return {'format':'bse_equity_master_tables_v1','texts':texts,'tables':tables}
