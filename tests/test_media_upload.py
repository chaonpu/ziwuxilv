import base64, copy, hashlib, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import community_media as m

def fixture(count=1):
    out=io.BytesIO()
    Image.new('RGB',(120,80),'gold').save(out,'WEBP',exif=b'private metadata')
    raw=out.getvalue()
    actor=dict(id=123,login='user_a',type='User')
    request=dict(version=1,request_id='a'*32,images=[dict(sha256=hashlib.sha256(raw).hexdigest(),parts=1) for _ in range(count)])
    body=m.REQUEST+json.dumps(request)
    issue=dict(number=5,state='open',user=actor,body=body)
    seal=dict(id=9,user=actor,body=m.SUBMIT+json.dumps(dict(request_id='a'*32,body_sha256=hashlib.sha256(body.encode()).hexdigest())))
    chunks=[dict(user=actor,body=m.CHUNK+json.dumps(dict(request_id='a'*32,image=i,part=0,data=base64.b64encode(raw).decode()))) for i in range(count)]
    event=dict(repository=dict(full_name=m.REPOSITORY),issue=dict(number=5),comment=seal,sender=actor)
    return event,issue,seal,chunks+[seal]

class ValidationTests(unittest.TestCase):
    def test_complex_valid_image_is_not_inflated_past_upload_limit(self):
        out=io.BytesIO()
        Image.effect_noise((800,600),100).convert('RGB').save(out,'WEBP',quality=20)
        raw=out.getvalue()
        self.assertLessEqual(len(raw),m.LIMIT)
        clean,size=m.clean_image(raw)
        self.assertEqual((800,600),size)
        self.assertLessEqual(len(clean),m.LIMIT)
    def test_one_and_three_images_preserve_shape_and_strip_metadata(self):
        for count in (1,3):
            owner,request,decoded=m.verify(*fixture(count))
            self.assertEqual(123,owner);self.assertEqual(count,len(decoded))
            with Image.open(io.BytesIO(decoded[0][0])) as image:
                self.assertEqual((120,80),image.size);self.assertNotIn('exif',image.info)
    def test_spoofed_sender_and_seal_owner_and_repository_rejected(self):
        for target in ('sender','seal','repository','pr','issue'):
            e,i,s,c=fixture()
            if target=='sender':e['sender']=dict(id=456)
            if target=='seal':s['user']=dict(id=456)
            if target=='repository':e['repository']['full_name']='attacker/repo'
            if target=='pr':i['pull_request']={}
            if target=='issue':e['issue']['number']=999
            # Presence of pull_request is rejected even for a malicious empty JSON object.
            if target=='pr':i['pull_request']={'url':'bad'}
            with self.assertRaises(m.MediaError):m.verify(e,i,s,c)
    def test_modified_body_after_seal_rejected(self):
        e,i,s,c=fixture();i['body']+=' '
        with self.assertRaises(m.MediaError):m.verify(e,i,s,c)
    def test_foreign_chunks_cannot_supply_missing_parts(self):
        e,i,s,c=fixture();c[0]['user']=dict(id=456)
        with self.assertRaises(m.MediaError):m.verify(e,i,s,c)
    def test_missing_conflicting_extra_and_corrupt_parts_rejected(self):
        for mode in ('missing','conflict','extra','corrupt'):
            e,i,s,c=fixture()
            if mode=='missing':c.pop(0)
            if mode=='conflict':
                duplicate=copy.deepcopy(c[0]);duplicate['body']=duplicate['body'].replace('data": "','data": "AAAA');c.append(duplicate)
            if mode=='extra':
                extra=copy.deepcopy(c[0]);extra['body']=extra['body'].replace('"part": 0','"part": 1');c.append(extra)
            if mode=='corrupt':c[0]['body']=c[0]['body'].replace('data": "','data": "!')
            with self.assertRaises(m.MediaError):m.verify(e,i,s,c)
    def test_png_oversize_animated_and_zero_bytes_rejected(self):
        out=io.BytesIO();Image.new('RGB',(10,10)).save(out,'PNG')
        animated=io.BytesIO()
        Image.new('RGB',(10,10),'red').save(animated,'WEBP',save_all=True,append_images=[Image.new('RGB',(10,10),'blue')])
        big=io.BytesIO();Image.new('RGB',(1281,1)).save(big,'WEBP')
        for raw in (b'',b'x'*(m.LIMIT+1),out.getvalue(),animated.getvalue(),big.getvalue()):
            with self.assertRaises(m.MediaError):m.clean_image(raw)
    def test_four_images_and_path_in_request_id_rejected(self):
        with self.assertRaises(m.MediaError):m.verify(*fixture(4))
        e,i,s,c=fixture();i['body']=i['body'].replace('a'*32,'../profiles/123');s['body']=m.SUBMIT+json.dumps(dict(request_id='../profiles/123',body_sha256=hashlib.sha256(i['body'].encode()).hexdigest()))
        with self.assertRaises(m.MediaError):m.verify(e,i,s,c)

class WriterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='media-writer-test-');self.root=Path(self.temp.name)
        self.previous=Path.cwd();self.remote=self.root/'remote.git';self.repo=self.root/'work'
        self.git('init','--bare',str(self.remote));self.git('clone',str(self.remote),str(self.repo));os.chdir(self.repo)
        self.git('checkout','-b','main')
        Path('profiles').mkdir();Path('profiles/index.json').write_text('existing registry')
        Path('release.txt').write_text('keep public release')
        self.git('add','.');self.commit('fixture');self.git('push','origin','main')
        self.event,self.issue,self.seal,self.comments=fixture(3);self.results=[]
    def tearDown(self):
        os.chdir(self.previous);assert self.root.resolve()==Path(self.temp.name).resolve();self.temp.cleanup()
    def git(self,*args):
        return subprocess.run(['git',*args],check=True,capture_output=True,text=True,encoding='utf-8').stdout.strip()
    def commit(self,message):
        self.git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m',message)
    def api(self,path,method='GET',body=None):
        if method=='GET' and '/issues/comments/' in path:return copy.deepcopy(self.seal)
        if method=='GET' and '/comments?' in path:return copy.deepcopy(self.comments)
        if method=='GET':return copy.deepcopy(self.issue)
        if method=='POST':
            result=json.loads(body['body'][len(m.RESULT):])
            if result['status']=='success':
                receipt=json.loads(self.git('--git-dir='+str(self.remote),'show','main:media/requests/123/'+ 'a'*32+'.json'))
                self.assertEqual(result['images'],receipt['images'])
                for image in result['images']:
                    self.assertTrue(self.git('--git-dir='+str(self.remote),'ls-tree','main',image['path']))
            self.results.append(result);self.comments.append(dict(user=dict(id=41898282,type='Bot'),body=body['body']));return {}
        self.issue['state']='closed';return {}
    def test_atomic_push_and_idempotent_replay_preserve_other_files(self):
        with patch.object(m,'api',self.api):m.process(self.event)
        head=self.git('rev-parse','HEAD')
        self.assertEqual(head,self.results[0]['commit_sha'])
        self.assertEqual('existing registry',Path('profiles/index.json').read_text())
        self.assertEqual('keep public release',Path('release.txt').read_text())
        changed=set(self.git('diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines())
        self.assertEqual(5,len(changed));self.assertIn("media/images.json",changed);self.assertTrue(all(x.startswith('media/') for x in changed))
        with patch.object(m,'api',self.api):m.process(self.event)
        self.assertEqual(head,self.git('rev-parse','HEAD'));self.assertEqual(1,len(self.results))
    def test_failed_ack_recovers_committed_success(self):
        original=self.api
        def fail_ack(path,method='GET',body=None):
            if method=='POST':raise OSError('lost ack')
            return original(path,method,body)
        with patch.object(m,'api',fail_ack):
            with self.assertRaises(OSError):m.process(self.event)
        with patch.object(m,'api',self.api):m.report_failure(self.event)
        self.assertEqual('success',self.results[0]['status'])
    def test_failed_push_cannot_ack_success(self):
        original=m.shell
        def fail_push(*args):
            if args[:2]==('git','push'):raise subprocess.CalledProcessError(1,args)
            return original(*args)
        with patch.object(m,'api',self.api),patch.object(m,'shell',fail_push):
            with self.assertRaises(subprocess.CalledProcessError):m.process(self.event)
        self.assertFalse(self.results);self.assertEqual('open',self.issue['state'])
        with patch.object(m,'api',self.api):m.report_failure(self.event)
        self.assertEqual('MEDIA_UPLOAD_FAILED',self.results[0]['error'])
    def test_concurrent_main_change_preserved_on_push_retry(self):
        original=m.shell;attempts=[]
        def conflict_once(*args):
            if args[:2]==('git','push') and not attempts:
                attempts.append(1);other=self.root/'other'
                self.git('clone','--branch','main',str(self.remote),str(other))
                previous=Path.cwd();os.chdir(other)
                Path('profiles/index.json').write_text('concurrent profile')
                self.git('add','.');self.commit('concurrent');self.git('push','origin','main');os.chdir(previous)
            return original(*args)
        with patch.object(m,'api',self.api),patch.object(m,'shell',conflict_once):m.process(self.event)
        self.assertEqual('concurrent profile',Path('profiles/index.json').read_text())
        self.assertEqual('success',self.results[0]['status'])
    def test_request_id_reuse_with_different_image_hash_rejected(self):
        with patch.object(m,'api',self.api):m.process(self.event)
        receipt='media/requests/123/'+ 'a'*32+'.json'
        result=m.load_json(receipt);result['images'][0]['upload_sha256']='b'*64
        m.write_json(receipt,result);self.git('add','.');self.commit('tampered fixture');self.git('push','origin','main')
        with patch.object(m,'api',self.api):
            with self.assertRaises(m.MediaError):m.process(self.event)

if __name__=='__main__':unittest.main()

