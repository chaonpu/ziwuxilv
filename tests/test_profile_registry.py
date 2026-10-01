import base64, hashlib, io, json, sys, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import profile_registry as p
from PIL import Image

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 1, 11, 30, tzinfo=timezone.utc)
        self.index = {'version': 1, 'profiles': {}}
    def request(self, nickname='海风', action='keep', token='a'):
        return dict(version=1, request_id=token*32, nickname=nickname, avatar_action=action)
    def update(self, uid=123, request=None, now=None, avatar=None, index=None):
        return p.apply_update(self.index if index is None else index, uid, 'user_a', request or self.request(), avatar, now or self.now)
    def error(self, code, function):
        with self.assertRaises(p.ProfileError) as e: function()
        self.assertEqual(code, e.exception.code)
        return e.exception
    def webp(self):
        out = io.BytesIO(); Image.new('RGB', (256, 256), (40, 80, 160)).save(out, 'WEBP')
        return out.getvalue()
    def fixture(self, request=None, actor=123):
        request = request or self.request()
        body = p.REQUEST_PREFIX + json.dumps(request, ensure_ascii=False)
        user = dict(id=actor, login='user_a', type='User')
        issue = dict(number=5, user=user, state='open', body=body)
        seal = dict(id=9, user=user, body=p.SUBMIT_PREFIX+json.dumps(dict(request_id=request['request_id'], body_sha256=hashlib.sha256(body.encode()).hexdigest())))
        event = dict(repository=dict(full_name=p.REPOSITORY), issue=dict(number=5), comment=seal, sender=user)
        return event, issue, seal, []
    def test_case_normalization(self):
        for text in (' Chao ', 'chao', 'CHAO', 'ＣＨＡＯ'):
            self.assertEqual('chao', p.normalize(text)[1])
    def test_spaces_cjk_and_unicode(self):
        self.assertEqual(('海 风', '海 风'), p.normalize('　海\u00a0  风  '))
        self.assertEqual(p.normalize('Café'), p.normalize('Cafe\u0301'))
        self.assertEqual(('子午', '子午'), p.normalize('子午'))
        self.assertEqual('管理员', p.normalize('管\u200b理员')[1])
    def test_invalid_blank_symbols_and_codepoint_limits(self):
        for text in (' ', '！＠＃', '😀😀', 'a', 'a'*17):
            self.error('INVALID_NICKNAME', lambda: p.normalize(text))
        self.assertEqual(16, len(p.normalize('海'*16)[0]))
    def test_reserved_variants_and_admin(self):
        for name in ('管理员', '官方', '系统', '子午汐律', ' ZiWuXiLv ', 'ＺｉＷｕＸｉＬｖ', '官-方', 'admin account'):
            self.error('RESERVED_NICKNAME', lambda: self.update(request=self.request(name)))
            self.assertEqual(p.normalize(name)[0], self.update(uid=p.ADMIN_ID, request=self.request(name))[1]['nickname'])
        self.assertTrue(p.reserved('meridian market rhythm'))
        # Administrator exemptions do not lift the 16-codepoint format limit.
        self.error('INVALID_NICKNAME', lambda: self.update(uid=p.ADMIN_ID, request=self.request('Meridian Market Rhythm')))
    def test_unique_and_self_nickname_when_avatar_changes(self):
        updated, profile, _ = self.update(request=self.request('子午'))
        self.error('NICKNAME_ALREADY_USED', lambda: self.update(uid=456, request=self.request('子午', token='b'), index=updated))
        out, changed, avatar = self.update(request=self.request('子午', 'replace', 'b'), index=updated, now=self.now+timedelta(days=30), avatar=self.webp())
        self.assertEqual('子午', changed['nickname']); self.assertIsNotNone(avatar)
    def test_case_insensitive_global_unique(self):
        index, _, _ = self.update(request=self.request(' Chao '))
        self.error('NICKNAME_ALREADY_USED', lambda: self.update(uid=456, request=self.request('CHAO', token='b'), index=index))
    def test_first_edit_and_thirty_exact_days(self):
        index, profile, _ = self.update()
        self.assertEqual(self.now+timedelta(days=30), p.timestamp(profile['next_edit_at']))
        for days in (10, 29):
            self.error('PROFILE_EDIT_COOLDOWN', lambda: self.update(index=index, request=self.request('山风', token='b'), now=self.now+timedelta(days=days)))
        self.error('PROFILE_EDIT_COOLDOWN', lambda: self.update(index=index, request=self.request('山风', token='b'), now=self.now+timedelta(days=30, microseconds=-1)))
        self.assertEqual('山风', self.update(index=index, request=self.request('山风', token='b'), now=self.now+timedelta(days=30))[1]['nickname'])
    def test_avatar_only_consumes_same_cooldown(self):
        index, _, _ = self.update(request=self.request('海风', 'replace'), avatar=self.webp())
        self.error('PROFILE_EDIT_COOLDOWN', lambda: self.update(index=index, request=self.request('海风', 'remove', 'b'), now=self.now+timedelta(days=10)))
    def test_server_derives_cooldown_from_updated_at(self):
        index, _, _ = self.update(); index['profiles']['123']['next_edit_at'] = '2000-01-01T00:00:00Z'
        self.error('PROFILE_EDIT_COOLDOWN', lambda: self.update(index=index, request=self.request('山风', token='b'), now=self.now+timedelta(days=10)))
    def test_admin_exemption_but_not_unique_exemption(self):
        index, _, _ = self.update(uid=p.ADMIN_ID, request=self.request('管理员'))
        index, _, _ = self.update(uid=p.ADMIN_ID, index=index, request=self.request('官方', token='b'))
        index, _, _ = self.update(uid=123, index=index, request=self.request('海风', token='c'))
        self.error('NICKNAME_ALREADY_USED', lambda: self.update(uid=p.ADMIN_ID, index=index, request=self.request('海风', token='d')))
    def test_idempotent_retry_and_no_change(self):
        index, original, _ = self.update()
        _, retry, _ = self.update(index=index)
        self.assertEqual(original, retry)
        self.error('PROFILE_NO_CHANGE', lambda: self.update(index=index, request=self.request(token='b'), now=self.now+timedelta(days=30)))
    def test_real_author_id_and_changed_login(self):
        request = self.request(); request['github_login'] = 'admin'
        event, issue, seal, comments = self.fixture(request)
        uid, login, _, _ = p.verified_request(event, issue, seal, comments)
        self.assertEqual((123, 'user_a'), (uid, login))
        request['github_id'] = p.ADMIN_ID
        self.error('PROFILE_AUTH_FAILED', lambda: p.verified_request(*self.fixture(request)))
    def test_other_sender_cannot_modify_issue_author(self):
        event, issue, seal, comments = self.fixture()
        event['sender'] = dict(id=456)
        self.error('PROFILE_AUTH_FAILED', lambda: p.verified_request(event, issue, seal, comments))
    def test_modified_issue_after_seal_rejected(self):
        event, issue, seal, comments = self.fixture()
        issue['body'] += ' '
        self.error('PROFILE_AUTH_FAILED', lambda: p.verified_request(event, issue, seal, comments))
    def test_avatar_format_dimensions_size_and_metadata(self):
        raw = self.webp(); clean = p.avatar_bytes(raw)
        with Image.open(io.BytesIO(clean)) as image:
            self.assertEqual((256, 256), image.size); self.assertEqual('WEBP', image.format)
            self.assertNotIn('exif', image.info)
        for invalid in (b'bad', b'a'*(p.MAX_AVATAR+1)):
            self.error('AVATAR_TOO_LARGE', lambda: p.avatar_bytes(invalid))
        out = io.BytesIO(); Image.new('RGB', (512, 512)).save(out, 'WEBP')
        self.error('AVATAR_TOO_LARGE', lambda: p.avatar_bytes(out.getvalue()))
    def test_chunked_avatar_authentication_and_hash(self):
        raw = self.webp(); request = self.request(action='replace')
        request.update(avatar_parts=1, avatar_sha256=hashlib.sha256(raw).hexdigest())
        event, issue, seal, comments = self.fixture(request)
        comments.append(dict(user=dict(id=123), body=p.CHUNK_PREFIX+json.dumps(dict(request_id=request['request_id'], index=0, data=base64.b64encode(raw).decode()))))
        self.assertIsNotNone(p.verified_request(event, issue, seal, comments)[3])
        comments[0]['user']['id'] = 456
        self.error('AVATAR_TOO_LARGE', lambda: p.verified_request(event, issue, seal, comments))
    def test_latest_registry_race_loser_is_rejected(self):
        winner, _, _ = self.update(uid=456, request=self.request('海风', token='b'))
        self.error('NICKNAME_ALREADY_USED', lambda: self.update(index=winner))

if __name__ == '__main__': unittest.main()
