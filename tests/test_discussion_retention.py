import json, sys, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import image_lifecycle as m


class DiscussionRetentionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc)

    def post(self, key='post1', age_days=91, category='General', author=123, body=''):
        return dict(
            id=key,
            type='post',
            post_id=key,
            number=1,
            body=body,
            author={'databaseId': author},
            createdAt=(self.now - timedelta(days=age_days)).isoformat(),
            category={'name': category},
        )

    def test_only_ordinary_posts_older_than_90_days_expire(self):
        old = self.post()
        self.assertTrue(m.should_expire_discussion(old, [old], set(), self.now))
        recent = self.post(key='recent', age_days=89)
        self.assertFalse(m.should_expire_discussion(recent, [recent], set(), self.now))
        exactly = self.post(key='exact', age_days=90)
        self.assertFalse(m.should_expire_discussion(exactly, [exactly], set(), self.now))

    def test_pinned_and_protected_categories_never_expire(self):
        pinned = self.post(key='pinned')
        self.assertFalse(m.should_expire_discussion(pinned, [pinned], {'pinned'}, self.now))
        for category in ('公告', '社区规则', 'Announcements', 'Rules'):
            row = self.post(key='protected-' + str(abs(hash(category))), category=category)
            self.assertFalse(m.should_expire_discussion(row, [row], set(), self.now))

    def test_only_admin_keep_marker_protects_discussion(self):
        old = self.post()
        admin_comment = dict(id='c1', type='comment', post_id='post1', body='#永久保留',
            author={'databaseId': m.ADMIN_ID})
        self.assertFalse(m.should_expire_discussion(old, [old, admin_comment], set(), self.now))
        admin_hidden = dict(id='c2', type='comment', post_id='post1',
            body='保留此帖 <!-- ZIWUXILV_RETAIN -->', author={'databaseId': m.ADMIN_ID})
        self.assertFalse(m.should_expire_discussion(old, [old, admin_hidden], set(), self.now))
        forged = dict(id='c3', type='comment', post_id='post1', body='#永久保留',
            author={'databaseId': 999})
        self.assertTrue(m.should_expire_discussion(old, [old, forged], set(), self.now))

    def test_discussion_tree_includes_post_comments_and_replies(self):
        records = [
            self.post(),
            dict(id='c1', type='comment', post_id='post1'),
            dict(id='r1', type='reply', post_id='post1', parent_id='c1'),
            self.post(key='post2'),
            dict(id='c2', type='comment', post_id='post2'),
        ]
        self.assertEqual({'post1', 'c1', 'r1'}, m.discussion_tree_ids('post1', records))

    def test_deleted_tree_removes_exclusive_media_but_preserves_shared_media(self):
        value = {'version': 1, 'images': {}, 'deleted_contents': {}}
        exclusive = m.register(value, 'media/123/' + 'a' * 32 + '/0.webp', 123, '1' * 64, self.now.isoformat())
        shared = m.register(value, 'media/123/' + 'b' * 32 + '/0.webp', 123, '2' * 64, self.now.isoformat())
        old = self.post(body='<!-- ZIWUXILV_IMAGES_V1:' + json.dumps([exclusive]) + ' -->')
        comment = dict(id='c1', type='comment', post_id='post1',
            body='<!-- ZIWUXILV_IMAGES_V1:' + json.dumps([shared]) + ' -->', author={'databaseId': 123})
        survivor = self.post(key='post2', age_days=1,
            body='<!-- ZIWUXILV_IMAGES_V1:' + json.dumps([shared]) + ' -->')
        before = m.reconcile(value, [old, comment, survivor], self.now.isoformat())
        affected = m.discussion_tree_ids('post1', [old, comment, survivor])
        image_ids = [iid for iid, entry in before['images'].items()
            if any(ref['id'] in affected for ref in entry['references'])]

        after = m.reconcile(before, [survivor], self.now.isoformat())
        after, removals = m.collect(after, self.now, immediate=image_ids)

        self.assertNotIn(exclusive, after['images'])
        self.assertIn(shared, after['images'])
        self.assertEqual([{'type': 'post', 'id': 'post2'}], after['images'][shared]['references'])
        self.assertEqual(['media/123/' + 'a' * 32 + '/0.webp'], removals)


if __name__ == '__main__':
    unittest.main()
