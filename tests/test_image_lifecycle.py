import copy, hashlib, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import image_lifecycle as m

class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.value={'version':1,'images':{},'deleted_contents':{}}
        self.path='media/123/'+'a'*32+'/0.webp'
        self.time=datetime(2026,10,2,tzinfo=timezone.utc)
        self.iid=m.register(self.value,self.path,123,'b'*64,self.time.isoformat())
    def content(self,key,kind='post'):
        return dict(id=key,type=kind,body='<!-- ZIWUXILV_IMAGES_V1:'+json.dumps([self.iid])+' -->',author={'databaseId':123})
    def test_single_image_and_final_shared_reference_removed(self):
        value=m.reconcile(self.value,[self.content('post1'),self.content('reply1','reply')])
        self.assertEqual(2,len(value['images'][self.iid]['references']))
        value=m.reconcile(value,[self.content('reply1','reply')]);value,paths=m.collect(value,immediate=[self.iid])
        self.assertEqual([],paths);self.assertIn(self.iid,value['images'])
        value=m.reconcile(value,[]);value,paths=m.collect(value,immediate=[self.iid])
        self.assertEqual([self.path],paths);self.assertNotIn(self.iid,value['images'])
    def test_orphan_gc_requires_grace_and_second_complete_scan(self):
        value,paths=m.collect(self.value,self.time+timedelta(days=3));self.assertEqual([],paths)
        value,paths=m.collect(value,self.time+timedelta(days=4));self.assertEqual([],paths)
        value,paths=m.collect(value,self.time+timedelta(days=6));self.assertEqual([self.path],paths)
    def test_admin_other_post_and_user_own_reply_permissions(self):
        self.assertTrue(m.authorized(m.ADMIN_ID,self.content('other')))
        self.assertTrue(m.authorized(123,self.content('post')))
        self.assertFalse(m.authorized(456,self.content('post')))
        self.assertFalse(m.authorized(123,self.content('avatar','avatar')))
        self.assertTrue(m.authorized(123,self.content('own','reply')))
        self.assertFalse(m.authorized(456,self.content('foreign','comment')))
    def test_avatar_shared_path_not_unlinked_when_current_avatar_survives(self):
        value={'images':{}}
        old=m.register(value,'profiles/123/avatar.webp',123,'a'*64,self.time.isoformat())
        current=m.register(value,'profiles/123/avatar.webp',123,'c'*64,self.time.isoformat())
        value['images'][current]['references']=[{'type':'avatar','id':'123'}]
        value,paths=m.collect(value,immediate=[old]);self.assertEqual([],paths);self.assertIn(current,value['images'])
    def test_unknown_reference_and_unsafe_path_stop_cleanup(self):
        with self.assertRaises(m.LifecycleError):m.reconcile({'images':{}},[self.content('x')])
        with self.assertRaises(m.LifecycleError):m.register(self.value,'../release.apk',123,'a'*64)
    def test_pagination_failure_does_not_yield_complete_inventory(self):
        with self.assertRaises(m.LifecycleError):list(m.pages(lambda cursor:{'nodes':[], 'pageInfo':{'hasNextPage':True,'endCursor':'same'}}))
    def test_inventory_scans_every_post_comment_reply_page_and_live_avatar(self):
        def connection(ids, cursor=None):
            return dict(nodes=[dict(id=x,body='',author={'databaseId':123}) for x in ids],
                pageInfo=dict(hasNextPage=cursor is not None,endCursor=cursor))
        def graphql(query,variables=None):
            if variables is None:return {'repository':{'id':'repo'}}
            if ' discussions(' in query:
                return {'repository':{'discussions':connection(['post1'],'d') if variables['c'] is None else connection(['post2'])}}
            if ' comments(' in query:
                rows=connection([]) if variables['id']=='post1' else connection(['comment1'],'c') if variables['c'] is None else connection(['comment2'])
                return {'node':{'comments':rows}}
            rows=connection([]) if variables['id']=='comment2' else connection(['reply1'],'r') if variables['c'] is None else connection(['reply2'])
            return {'node':{'replies':rows}}
        with patch.object(m,'graphql',graphql),patch.object(m,'load_json',return_value={'profiles':{'123':{'avatar_path':'profiles/123/avatar.webp','avatar_sha256':'a'*64}}}):
            rows=m.inventory()
        self.assertEqual({'post1','post2','comment1','comment2','reply1','reply2','123'},{r['id'] for r in rows})
        self.assertEqual('comment1',next(r for r in rows if r['id']=='reply2')['parent_id'])

class DeleteWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='lifecycle-test-');self.root=Path(self.tmp.name);self.previous=Path.cwd()
        self.remote=self.root/'remote.git';self.repo=self.root/'work'
        self.git('init','--bare',str(self.remote));self.git('clone',str(self.remote),str(self.repo));os.chdir(self.repo)
        self.git('checkout','-b','main');Path('profiles').mkdir();m.write_json('profiles/index.json',{'profiles':{}})
        self.path='media/123/'+'a'*32+'/0.webp';Path(self.path).parent.mkdir(parents=True);Path(self.path).write_bytes(b'image')
        value={'images':{},'deleted_contents':{}};self.iid=m.register(value,self.path,123,hashlib.sha256(b'image').hexdigest());m.write_json(m.REGISTRY,value)
        self.git('add','.');self.git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture');self.git('push','origin','main')
        actor={'id':m.ADMIN_ID,'login':'admin','type':'User'}
        req={'version':1,'request_id':'d'*32,'action':'delete','content_id':'post1'};body=m.REQUEST+json.dumps(req)
        self.issue={'number':5,'user':actor,'body':body};self.seal={'id':9,'user':actor,'body':m.SUBMIT+json.dumps({'request_id':'d'*32,'body_sha256':hashlib.sha256(body.encode()).hexdigest()})}
        self.event={'repository':{'full_name':m.REPOSITORY},'issue':{'number':5},'sender':actor,'comment':self.seal}
        self.records=[dict(id='post1',type='post',post_id='post1',body='<!-- ZIWUXILV_IMAGES_V1:'+json.dumps([self.iid])+' -->',author={'databaseId':123})];self.results=[]
    def tearDown(self):
        os.chdir(self.previous);self.tmp.cleanup()
    def git(self,*args):return subprocess.run(['git',*args],check=True,capture_output=True,text=True,encoding='utf-8').stdout.strip()
    def api(self,path,method='GET',body=None):
        if method=='GET':return copy.deepcopy(self.seal if '/issues/comments/' in path else self.issue)
        if method=='POST':self.results.append(json.loads(body['body'][len(m.RESULT):]))
        return {}
    def graphql(self,query,variables):
        self.records=[];return {'deleteDiscussion':{'discussion':{'id':'post1'}}}
    def test_admin_other_post_delete_blob_and_receipt_atomic_and_replay_safe(self):
        with patch.object(m,'api',self.api),patch.object(m,'inventory',lambda:copy.deepcopy(self.records)),patch.object(m,'graphql',self.graphql):
            m.process(self.event);head=self.git('rev-parse','HEAD');m.process(self.event)
        self.assertFalse(Path(self.path).exists());self.assertNotIn(self.iid,m.registry()['images'])
        self.assertIn('post1',m.registry()['deleted_contents']);self.assertEqual(head,self.git('rev-parse','HEAD'))
        self.assertEqual('success',self.results[-1]['status'])
    def as_author(self,owner):
        actor={'id':owner,'login':'owner','type':'User'}
        self.issue['user']=actor;self.seal['user']=actor
        self.event.update(sender=actor,comment=self.seal)
    def test_author_deletes_own_post_and_descendants_preserving_shared_image(self):
        self.as_author(123)
        self.records.extend([
            dict(id='reply1',type='reply',post_id='post1',parent_id='comment1',body=self.records[0]['body'],author={'databaseId':456}),
            dict(id='outside',type='post',post_id='outside',body=self.records[0]['body'],author={'databaseId':789})])
        def delete(query,variables):
            self.assertEqual('post1',variables['id'])
            self.records=[r for r in self.records if r['post_id']!='post1']
            return {'deleteDiscussion':{'discussion':{'id':'post1'}}}
        with patch.object(m,'api',self.api),patch.object(m,'inventory',lambda:copy.deepcopy(self.records)),patch.object(m,'graphql',delete):
            m.process(self.event)
        self.assertEqual('success',self.results[-1]['status'])
        self.assertTrue(Path(self.path).exists())
        self.assertIn('post1',m.registry()['deleted_contents'])
        self.assertIn('reply1',m.registry()['deleted_contents'])
        self.assertNotIn('outside',m.registry()['deleted_contents'])
        self.assertEqual([{'type':'post','id':'outside'}],m.registry()['images'][self.iid]['references'])
    def test_authenticated_other_user_cannot_delete_post(self):
        self.as_author(456)
        with patch.object(m,'api',self.api),patch.object(m,'inventory',lambda:copy.deepcopy(self.records)),patch.object(m,'graphql') as mutation:
            with self.assertRaises(m.LifecycleError):m.process(self.event)
            mutation.assert_not_called()
        self.assertTrue(Path(self.path).exists())
        self.assertNotIn('post1',m.registry()['deleted_contents'])

    def test_failed_content_delete_keeps_blob_and_all_references_then_retry_finishes(self):
        with patch.object(m,'api',self.api),patch.object(m,'inventory',lambda:copy.deepcopy(self.records)),patch.object(m,'graphql',side_effect=m.LifecycleError('offline')):
            with self.assertRaises(m.LifecycleError):m.process(self.event)
        self.assertTrue(Path(self.path).exists());self.assertEqual(1,len(m.registry()['images'][self.iid]['references']));self.assertEqual([],self.results)
        with patch.object(m,'api',self.api),patch.object(m,'inventory',lambda:copy.deepcopy(self.records)),patch.object(m,'graphql',self.graphql):m.process(self.event)
        self.assertFalse(Path(self.path).exists())
    def test_incomplete_scan_never_mutates_content_or_storage(self):
        with patch.object(m,'api',self.api),patch.object(m,'inventory',side_effect=m.LifecycleError('truncated')),patch.object(m,'graphql') as mutation:
            with self.assertRaises(m.LifecycleError):m.process(self.event)
            mutation.assert_not_called()
        self.assertTrue(Path(self.path).exists());self.assertEqual([],self.results)
    def test_spoofed_actor_and_arbitrary_image_id_cannot_delete(self):
        self.event['sender']={'id':456}
        with self.assertRaises(m.LifecycleError):m.verified_request(self.event,self.issue,self.seal)
        self.event['sender']=self.issue['user'];self.issue['body']=self.issue['body'].replace('post1',self.iid)
        with self.assertRaises(m.LifecycleError):m.verified_request(self.event,self.issue,self.seal)
    def test_ack_loss_recovers_success_instead_of_reporting_failed_deletion(self):
        def lost_ack(path,method='GET',body=None):
            if method=='POST':raise OSError('lost response')
            return self.api(path,method,body)
        with patch.object(m,'api',lost_ack),patch.object(m,'inventory',lambda:copy.deepcopy(self.records)),patch.object(m,'graphql',self.graphql):
            with self.assertRaises(OSError):m.process(self.event)
        with patch.object(m,'api',self.api):m.report_failure(self.event)
        self.assertEqual('success',self.results[-1]['status']);self.assertFalse(Path(self.path).exists())

    def test_own_parent_wipe_preserves_other_users_reply_and_resumes_pending_intent(self):
        actor={'id':123,'login':'owner','type':'User'}
        body=self.issue['body'].replace('post1','comment1')
        self.issue.update(user=actor,body=body)
        self.seal.update(user=actor,body=m.SUBMIT+json.dumps({'request_id':'d'*32,'body_sha256':hashlib.sha256(body.encode()).hexdigest()}))
        self.event.update(sender=actor,comment=self.seal)
        reply_path='media/456/'+'b'*32+'/0.webp'
        Path(reply_path).parent.mkdir(parents=True);Path(reply_path).write_bytes(b'reply image')
        value=m.registry();reply_id=m.register(value,reply_path,456,hashlib.sha256(b'reply image').hexdigest());m.write_json(m.REGISTRY,value)
        self.git('add','.');self.git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','reply');self.git('push','origin','main')
        self.records=[dict(id='comment1',type='comment',post_id='post1',body='<!-- ZIWUXILV_IMAGES_V1:'+json.dumps([self.iid])+' -->',author={'databaseId':123},deletedAt=None),
            dict(id='reply1',type='reply',post_id='post1',parent_id='comment1',body='<!-- ZIWUXILV_IMAGES_V1:'+json.dumps([reply_id])+' -->',author={'databaseId':456},deletedAt=None)]
        def wipe(query,variables):
            self.records[0].update(body='',author=None,deletedAt='2026-10-01T19:00:00Z')
            return {'deleteDiscussionComment':{'comment':{'id':'comment1'}}}
        actual_commit=m.commit
        def fail_final(value,removals=(),receipt_path=None,receipt=None):
            if receipt and receipt.get('status')=='success':raise OSError('disconnected after wipe')
            actual_commit(value,removals,receipt_path,receipt)
        with patch.object(m,'api',self.api),patch.object(m,'inventory',lambda:copy.deepcopy(self.records)),patch.object(m,'graphql',wipe),patch.object(m,'commit',fail_final):
            with self.assertRaises(OSError):m.process(self.event)
        with patch.object(m,'api',self.api),patch.object(m,'inventory',lambda:copy.deepcopy(self.records)),patch.object(m,'graphql') as mutation:
            m.process(self.event);mutation.assert_not_called()
        self.assertFalse(Path(self.path).exists());self.assertTrue(Path(reply_path).exists())
        self.assertEqual(['comment1'],self.results[-1]['affected'])
        self.assertNotIn('reply1',m.registry()['deleted_contents'])
        self.assertEqual([{'type':'reply','id':'reply1'}],m.registry()['images'][reply_id]['references'])

if __name__=='__main__':unittest.main()
