"""Validate reviewed device approval and preserve an immutable active version."""
import json
import re
import sys
from pathlib import Path

def validate(request,active):
    for key in ('sourceCommit','versionName','versionCode','apkSha256','artifactSha256','signingCertificateSha256','updateLog','sourceUrl'):
        if key not in request:raise ValueError('Missing release field: '+key)
    if not re.fullmatch(r'[a-f0-9]{40}',request['sourceCommit']):raise ValueError('Invalid source commit')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',request['versionName']):raise ValueError('Invalid version name')
    if type(request['versionCode']) is not int or request['versionCode']<=0:raise ValueError('Invalid version code')
    for key in ('apkSha256','artifactSha256','signingCertificateSha256'):
        if not re.fullmatch(r'[a-f0-9]{64}',request[key]):raise ValueError('Invalid digest: '+key)
    if not request['sourceUrl'].startswith('https://') or any(c in request['sourceUrl'] for c in '\r\n'):raise ValueError('Invalid signed source URL')
    proof=request.get('deviceVerification',{})
    if proof.get('passed') is not True or not proof.get('evidence') or any(proof.get(k)!=request[k] for k in ('sourceCommit','versionCode','apkSha256')):
        raise ValueError('Independent install/start/upgrade approval must bind this source, version and signed APK hash')
    if active and int(active.get('versionCode',0))>request['versionCode']:raise ValueError('Older release cannot replace active version')
    if active and active.get('versionCode')==request['versionCode']:
        for key in ('sourceCommit','apkSha256','versionName','signingCertificateSha256'):
            if active.get(key)!=request[key]:raise ValueError('Active version is immutable: '+key)
    return True

if __name__=='__main__':
    active=Path(sys.argv[2]);validate(json.loads(Path(sys.argv[1]).read_text()),json.loads(active.read_text()) if active.exists() else {})
    print('Reviewed release approval verified')
