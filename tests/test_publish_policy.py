import copy
import unittest
from tools.publish_policy import validate

class PublishPolicyTests(unittest.TestCase):
    def request(self):
        req={'sourceCommit':'a'*40,'versionName':'1.2.3','versionCode':10,'apkSha256':'b'*64,'artifactSha256':'c'*64,'signingCertificateSha256':'d'*64,'sourceUrl':'https://example.test/apk.zip','updateLog':'Test'}
        req['deviceVerification']={'passed':True,'sourceCommit':req['sourceCommit'],'versionCode':req['versionCode'],'apkSha256':req['apkSha256'],'evidence':'manual exact-APK install/start/upgrade record'}
        return req
    def test_reviewed_release_and_exact_replay(self):
        req=self.request();self.assertTrue(validate(req,{}));self.assertTrue(validate(req,req))
    def test_missing_or_wrong_device_evidence_is_rejected(self):
        for key,value in [('passed',False),('apkSha256','e'*64),('sourceCommit','f'*40),('evidence','')]:
            req=self.request();req['deviceVerification'][key]=value
            with self.assertRaises(ValueError):validate(req,{})
    def test_same_version_cannot_change_bytes_or_source(self):
        req=self.request();active=copy.deepcopy(req);active['apkSha256']='e'*64
        with self.assertRaises(ValueError):validate(req,active)
    def test_older_version_is_rejected(self):
        req=self.request()
        with self.assertRaises(ValueError):validate(req,{'versionCode':11})

if __name__=='__main__':unittest.main()
