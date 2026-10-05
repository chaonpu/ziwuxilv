import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from access_state import ADMIN_ID, REPOSITORY, REQUEST, GRANT, DECISION, materialize, refresh_required

class AccessStateTests(unittest.TestCase):
    now = 1790985600000
    def issue(self, number=1):
        return dict(number=number, user=dict(id=42, login="u42"), body=REQUEST, created_at="2026-10-01T00:00:00Z")
    def comment(self, body, cid=1, author=ADMIN_ID, number=1):
        return dict(id=cid, user=dict(id=author), body=body, created_at="2026-10-01T00:00:00Z",
            issue_url=f"https://api.github.com/repos/{REPOSITORY}/issues/{number}")
    def row(self, comments):
        return materialize([self.issue()], {1: comments}, self.now)["records"][0]
    def test_genuine_admin_and_expiry_only(self):
        body = GRANT + '{"role":"member","days":1} -->'
        self.assertEqual("pending", self.row([self.comment(body, author=42)])["status"])
        row = self.row([self.comment(body)])
        self.assertEqual("approved", row["status"]); self.assertEqual(1790812800000 + 86400000, row["expiresAt"])
    def test_latest_revocation_wins_independent_of_comment_order(self):
        grant = self.comment(GRANT + '{"role":"member","days":null} -->', 1)
        revoke = self.comment(DECISION + 'revoked -->', 2)
        self.assertEqual("revoked", self.row([revoke, grant])["status"])
    def test_malformed_new_admin_grant_fails_closed(self):
        self.assertEqual("revoked", self.row([self.comment(GRANT + '{"role":"member","days":true} -->')])["status"])
    def test_foreign_issue_response_fails_before_publication(self):
        with self.assertRaises(ValueError): self.row([self.comment(DECISION + 'approved -->', number=2)])
    def test_earlier_registration_survives_new_pending_request(self):
        state = materialize([self.issue(2), self.issue(1)],
            {1:[self.comment(GRANT + '{"role":"registered","days":null} -->')], 2:[]}, self.now)
        row = state["records"][0]
        self.assertEqual(2, row["number"]); self.assertTrue(row["registrationRetained"])
        self.assertEqual("registered", row["authorizationRole"])
    def test_audit_events_refresh_even_when_state_is_fresh(self):
        state = dict(schemaVersion=1, repository=REPOSITORY, adminId=ADMIN_ID, generatedAt=self.now)
        for event in ("issues", "issue_comment", "push", "workflow_dispatch"):
            self.assertTrue(refresh_required(event, state, self.now))
    def test_heartbeat_events_skip_recent_state_and_refresh_stale_or_invalid_state(self):
        state = dict(schemaVersion=1, repository=REPOSITORY, adminId=ADMIN_ID, generatedAt=self.now)
        for event in ("workflow_run", "schedule"):
            self.assertFalse(refresh_required(event, state, self.now + 599999))
            self.assertTrue(refresh_required(event, state, self.now + 600000))
            self.assertTrue(refresh_required(event, state, self.now - 1))
            self.assertTrue(refresh_required(event, None, self.now))
            self.assertTrue(refresh_required(event, {**state, "adminId": 42}, self.now))

if __name__ == "__main__": unittest.main()
