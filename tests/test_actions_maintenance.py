import unittest
from datetime import datetime, timezone
from tools.actions_maintenance import plan

class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,5,tzinfo=timezone.utc)
        self.run={'id':1,'status':'completed','path':'.github/workflows/old.yml','updated_at':'2026-08-01T00:00:00Z'}
        self.artifact={'id':10,'name':'test-verified-'+'a'*40,'created_at':'2026-08-01T00:00:00Z','expired':False,'workflow_run':{'id':1}}
    def result(self,refs=None,artifacts=None,runs=None):
        return plan(artifacts or [self.artifact],runs or [self.run],set(),refs or [],self.now)
    def test_unknown_cross_repo_reference_retains_handoff_and_run(self):
        self.assertEqual(self.result()['candidates'],[])
    def test_acknowledged_old_handoff_can_be_removed(self):
        self.assertEqual(len(self.result([{'acknowledgedArtifactIds':[10]}])['candidates']),2)
    def test_pin_overrides_acknowledgement(self):
        self.assertEqual(self.result([{'acknowledgedArtifactIds':[10],'artifactId':10}])['candidates'],[])
    def test_busy_run_and_missing_run_are_protected(self):
        self.run['status']='in_progress'
        self.assertEqual(self.result([{'acknowledgedArtifactIds':[10]}])['candidates'],[])
        self.assertEqual(plan([self.artifact],[],set(),[],self.now)['candidates'],[])
    def test_current_workflow_run_is_never_removed(self):
        result=plan([], [self.run], {self.run['path']}, [],self.now)
        self.assertEqual(result['candidates'],[])
    def test_recent_orphan_run_gets_grace_period(self):
        self.run['updated_at']='2026-10-01T00:00:00Z'
        self.assertEqual(plan([], [self.run],set(),[],self.now)['candidates'],[])

if __name__=='__main__':unittest.main()
