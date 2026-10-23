"""Bounded official discovery, validated-source recovery and future-only updates.

This module never invents a calendar, changes a past date or turns a missing
provider schedule into known sessions. Cache hits retain their original time.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlencode
from calendar_runtime import canonical, require, validate


def fetch_retry(fetch, url, *, pause=time.sleep):
    """Retry transient transport errors only; never retry a verification wall."""
    for attempt in range(3):
        try:return fetch(url), attempt+1
        except (URLError, TimeoutError, ConnectionError) as exc:
            transient = not isinstance(exc, HTTPError) or exc.code in (408, 429, 500, 502, 503, 504)
            if not transient or attempt == 2:raise
            pause(.25*(attempt+1))


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__();self.links=[];self.link=None;self.hidden=0;self.feed(text)
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'):self.hidden+=1
        if tag=='a' and not self.hidden:self.link=[dict(attrs).get('href',''),[]]
    def handle_data(self, text):
        if self.link is not None and not self.hidden:self.link[1].append(text)
    def handle_endtag(self, tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
        if tag=='a' and self.link is not None:
            self.links.append((self.link[0],' '.join(''.join(self.link[1]).split())));self.link=None


def discover_documents(text, listing, patterns, *, label_pattern=r'holiday', limit=12):
    """Only registered official URL shapes can leave the discovery boundary."""
    found=[]
    for href,label in Links(text).links:
        url=urljoin(listing,unescape(href)).replace(' ','%20')
        parsed=urlparse(url)
        if (parsed.scheme=='https' and not parsed.username and not parsed.password
                and parsed.port in (None,443) and not parsed.fragment
                and re.search(label_pattern,label,re.I)
                and any(re.fullmatch(pattern,url) for pattern in patterns)):
            found.append(url)
    # Official CMS JSON responses embed the same download links in HTML strings.
    if text.lstrip().startswith(('{','[')):
        obj=json.loads(text)
        require(not isinstance(obj,dict) or not obj.get('errors'),'calendar_discovery_cms_error')
        def strings(value, depth=0):
            require(depth<=30,'calendar_discovery_json_too_deep')
            if isinstance(value,str):yield value
            elif isinstance(value,list):
                for v in value:yield from strings(v,depth+1)
            elif isinstance(value,dict):
                for v in value.values():yield from strings(v,depth+1)
        for value in strings(obj):
            if '<a' in value:
                found.extend(discover_documents(value,listing,patterns,label_pattern=label_pattern,limit=limit))
        def downloads(value,depth=0):
            require(depth<=30,'calendar_discovery_json_too_deep')
            if isinstance(value,dict):
                if isinstance(value.get('label'),str) and re.search(label_pattern,value['label'],re.I):
                    for url in strings(value.get('file',{})):
                        if any(re.fullmatch(p,url) for p in patterns):found.append(url)
                for child in value.values():downloads(child,depth+1)
            elif isinstance(value,list):
                for child in value:downloads(child,depth+1)
        downloads(obj)
    result=sorted(set(found))
    require(len(result)<=limit,'calendar_discovery_limit_exceeded')
    return result


def sgx_cms_url(raw):
    """Resolve SGX's public, versioned CMS endpoint from its own app config."""
    obj=json.loads(raw);version=obj.get('CMS_VERSION','')
    require(obj.get('endpoints',{}).get('CMS_API_URL')=='https://api2.sgx.com/content-api'
            and re.fullmatch('[0-9a-f]{40}',version),'sgx_public_cms_config_changed')
    return 'https://api2.sgx.com/content-api?'+urlencode({'queryId':version+':page',
        'variables':json.dumps({'path':'/fx-solutions/trading','lang':'EN'},separators=(',',':'))})


def future_record(old, proposed, cutoff):
    """Preserve today/history; unclear removals remain UNKNOWN in the future."""
    result=deepcopy(proposed);boundary=cutoff.isoformat()
    unknown=set(result['unknownDates'])
    groups=('holidays','overrides')
    for field in groups:
        result[field]={d:v for d,v in result[field].items() if d>boundary}
    unknown={d for d in unknown if d>boundary}
    if old:
        for field in groups:
            result[field].update({d:v for d,v in old[field].items() if d<=boundary})
        unknown.update(d for d in old['unknownDates'] if d<=boundary)
        # An annual page omitting a previously exceptional session does not
        # prove normal trading. Only explicit holidays/clocks resolve UNKNOWN.
        known=set(result['holidays'])|set(result['overrides'])
        for d in set(old['holidays'])|set(old['overrides'])|set(old['unknownDates']):
            if d>boundary and d not in known:unknown.add(d)
    else:
        cursor=date.fromisoformat(result['coverageStart'])
        while cursor<=min(cutoff,date.fromisoformat(result['coverageEnd'])):
            unknown.add(cursor.isoformat());cursor+=timedelta(days=1)
    result['unknownDates']=sorted(unknown-set(result['holidays'])-set(result['overrides']))
    return result


def cache_index(root):
    path=Path(root)/'calendars/source-cache/index.json'
    obj=json.loads(path.read_bytes()) if path.exists() else {'schemaVersion':1,'entries':{}}
    require(obj.get('schemaVersion')==1 and isinstance(obj.get('entries'),dict),'calendar_source_cache_index_invalid')
    return obj


def save_venue_cache(root, venue, url, source_hash, calendars, nights, now):
    """Call only after a strict venue parser succeeds, never on a cache hit."""
    require(re.fullmatch('[0-9a-f]{64}',source_hash) is not None,'calendar_source_cache_proof_missing')
    obj={'schemaVersion':1,'adapter':'china-futures-consensus-v1','venue':venue,
         'sourceUrl':url,'sourceSha256':source_hash,'calendars':calendars,'nightSuspensionDates':nights}
    raw=canonical(obj);digest=hashlib.sha256(raw).hexdigest()
    directory=Path(root)/'calendars/source-cache';directory.mkdir(parents=True,exist_ok=True)
    path=directory/(digest+'.json')
    if path.exists():require(path.read_bytes()==raw,'immutable_calendar_source_cache_conflict')
    else:path.write_bytes(raw)
    index=cache_index(root);key=hashlib.sha256(url.encode()).hexdigest()
    index['entries'][key]={'sha256':digest,'checkedAt':now.isoformat(timespec='seconds')}
    (directory/'index.json').write_bytes(canonical(index))
    return digest


def load_venue_cache(root, venue, url, now, *, max_age_days=14):
    require(type(max_age_days) is int and 1<=max_age_days<=30,'calendar_source_cache_age_invalid')
    entry=cache_index(root)['entries'].get(hashlib.sha256(url.encode()).hexdigest(),{})
    digest=entry.get('sha256','')
    require(re.fullmatch('[0-9a-f]{64}',digest) is not None,'calendar_source_cache_missing')
    stamp=datetime.fromisoformat(entry['checkedAt'])
    require(stamp.tzinfo is not None and 0<=(now-stamp).total_seconds()<=max_age_days*86400,'calendar_source_cache_expired')
    path=Path(root)/'calendars/source-cache'/(digest+'.json');raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==digest,'calendar_source_cache_digest_mismatch')
    obj=json.loads(raw)
    require(obj.get('schemaVersion')==1 and obj.get('adapter')=='china-futures-consensus-v1'
            and obj.get('venue')==venue and obj.get('sourceUrl')==url
            and re.fullmatch('[0-9a-f]{64}',obj.get('sourceSha256','')),'calendar_source_cache_scope_invalid')
    records=obj['calendars'];nights=obj['nightSuspensionDates']
    require(records and all(str(now.year)<=y<=str(now.year+1) for y in records),'calendar_source_cache_year_expired')
    # Re-validate the cached normalized structure independently of its hash.
    seed=json.loads(Path(__file__).with_name('calendar-seed.json').read_bytes())
    for year,record in records.items():
        require(record.get('coverageEnd')==year+'-12-31' and now.date()<=date.fromisoformat(record['coverageEnd']),
                'calendar_source_cache_year_expired')
        require(15<=len(record['holidays'])<=55 and len(set(record['holidays'].values()))==7,
                'calendar_source_cache_annual_incomplete')
    seed['calendars']['CN_FUTURES']['years']=deepcopy(records)
    for record in seed['calendars']['CN_FUTURES']['years'].values():record['sources']=[url]
    validate(canonical(seed))
    require(len(nights)==(0 if venue=='GFEX' else 7) and len(set(nights))==len(nights)
            and all(now.year-1<=date.fromisoformat(d).year<=now.year+1 for d in nights),'calendar_source_cache_nights_invalid')
    return records,nights,{'mode':'validated_source_cache','sourceUrl':url,'sourceSha256':obj['sourceSha256'],
                          'cacheSha256':digest,'sourceCheckedAt':entry['checkedAt']}
