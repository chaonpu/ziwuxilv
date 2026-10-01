"""Exercise the actual Git commit/push writer with fake GitHub API responses."""
import copy, hashlib, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import profile_registry as p

class WriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='profile-writer-test-')
        self.root = Path(self.temp.name)
        self.previous = Path.cwd()
        self.remote = self.root/'remote.git'; self.repo = self.root/'work'
        self.git('init', '--bare', str(self.remote))
        self.git('clone', str(self.remote), str(self.repo))
        os.chdir(self.repo)
        self.git('checkout', '-b', 'main')
        Path('profiles').mkdir(); p.write_json('profiles/index.json', {'version': 1, 'profiles': {}})
        Path('release-preserved.txt').write_text('keep original public release')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
        self.git('push', 'origin', 'main')
        self.request = dict(version=1, request_id='a'*32, nickname='海风', avatar_action='keep')
        body = p.REQUEST_PREFIX + json.dumps(self.request, ensure_ascii=False)
        user = dict(id=123, login='user_a', type='User')
        self.issue = dict(number=5, state='open', user=user, body=body)
        self.seal = dict(id=9, user=user, body=p.SUBMIT_PREFIX+json.dumps(dict(request_id='a'*32, body_sha256=hashlib.sha256(body.encode()).hexdigest())))
        self.event = dict(repository=dict(full_name=p.REPOSITORY), issue=dict(number=5), comment=self.seal, sender=user)
        self.comments = [self.seal]; self.results=[]
    def tearDown(self):
        os.chdir(self.previous)
        # Only remove this known, resolved tempfile tree.
        assert self.root.resolve() == Path(self.temp.name).resolve()
        self.temp.cleanup()
    def git(self, *args):
        return subprocess.run(['git', *args], check=True, capture_output=True, text=True, encoding='utf-8').stdout.strip()
    def api(self, path, method='GET', body=None):
        if method == 'GET' and '/issues/comments/' in path: return copy.deepcopy(self.seal)
        if method == 'GET' and '/comments?' in path: return copy.deepcopy(self.comments)
        if method == 'GET': return copy.deepcopy(self.issue)
        if method == 'POST':
            # Server must not acknowledge until the profile and receipt exist on remote main.
            remote_index = json.loads(self.git('--git-dir='+str(self.remote), 'show', 'main:profiles/index.json'))
            result = json.loads(body['body'][len(p.RESULT_PREFIX):]); self.results.append(result)
            if result['status'] == 'success': self.assertEqual('a'*32, remote_index['profiles']['123']['last_request_id'])
            self.comments.append(dict(user=dict(id=41898282, type='Bot'), body=body['body'])); return {}
        self.issue['state']='closed'; return {}
    def test_atomic_remote_commit_and_success_ack_then_idempotent_replay(self):
        with patch.object(p, 'api', self.api): p.process(self.event)
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual('success', self.results[0]['status'])
        self.assertEqual(head, self.results[0]['commit_sha'])
        self.assertEqual('keep original public release', Path('release-preserved.txt').read_text())
        self.assertEqual({'profiles/123/profile.json', 'profiles/index.json', 'profiles/requests/123/'+ 'a'*32+'.json'},
                         set(self.git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines()))
        with patch.object(p, 'api', self.api): p.process(self.event)
        self.assertEqual(head, self.git('rev-parse', 'HEAD'))
        self.assertEqual(1, len(self.results))
    def test_rechecks_newest_main_and_rejects_competing_nickname(self):
        remote_copy=self.root/'competitor'
        self.git('clone', '--branch', 'main', str(self.remote), str(remote_copy))
        previous=Path.cwd(); os.chdir(remote_copy)
        index, profile, _=p.apply_update({'version':1,'profiles':{}},456,'user_b',self.request,None,p.datetime.now(p.timezone.utc))
        p.write_json('profiles/index.json',index);self.git('add','.');self.git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','competing request');self.git('push','origin','main')
        os.chdir(previous)
        with patch.object(p,'api',self.api):p.process(self.event)
        self.assertEqual('NICKNAME_ALREADY_USED',self.results[0]['error'])
        self.assertFalse(Path('profiles/123/profile.json').exists())
    def test_failed_push_never_returns_success_or_marks_request_complete(self):
        actual=p.shell
        def fail_push(*args):
            if args[:2]==('git','push'):raise subprocess.CalledProcessError(1,args)
            return actual(*args)
        with patch.object(p,'api',self.api),patch.object(p,'shell',fail_push):
            with self.assertRaises(subprocess.CalledProcessError):p.process(self.event)
        self.assertEqual([],self.results);self.assertEqual('open',self.issue['state'])
        with patch.object(p,'api',self.api):p.report_failure(self.event)
        self.assertEqual('PROFILE_UPDATE_FAILED',self.results[0]['error'])

    def test_failed_ack_recovery_reports_committed_success(self):
        original=self.api
        def failed_ack(path,method='GET',body=None):
            if method=='POST':raise OSError('network lost after push')
            return original(path,method,body)
        with patch.object(p,'api',failed_ack):
            with self.assertRaises(OSError):p.process(self.event)
        with patch.object(p,'api',self.api):p.report_failure(self.event)
        self.assertEqual('success',self.results[0]['status'])

if __name__=='__main__':unittest.main()
