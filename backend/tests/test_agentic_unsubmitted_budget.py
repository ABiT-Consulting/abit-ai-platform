"""Preflight retries stay bounded without spending a model dispatch attempt."""
import test_agentic_controls_events as fixture


class PreflightBudget(fixture.Fixture):
    def setUp(self):
        super().setUp()
        self.u = self.load('agentic_unsubmitted')
        self.row = self.enqueue()

    def block(self):
        self.assertTrue(self.u.block(self.row['invocation_id'], 'technical preflight', self.path))

    def rearm(self):
        return self.u.rearm(self.row['invocation_id'], self.packet(), self.gate_value, 'human-retry', self.path)

    def test_three_preflight_retries_leave_first_actual_claim_available(self):
        for _ in range(3):
            self.block()
            self.assertEqual(0, self.rearm()['dispatch_attempts'])
        claimed = self.g.claim_invocation(self.row['invocation_id'], 'fixture-first-run',
                                          gate=self.gate_value, db_path=self.path)
        self.assertEqual(1, claimed['dispatch_attempts'])
        self.assertEqual('fixture-first-run', claimed['automation_run_id'])

    def test_fourth_preflight_retry_is_denied(self):
        for _ in range(3):
            self.block()
            self.rearm()
        self.block()
        with self.assertRaisesRegex(ValueError, 'retry limit'):
            self.rearm()

    def test_legacy_counter_is_normalized_only_with_exact_never_claimed_audit(self):
        self.block()
        with self.g._transaction(self.path) as db:
            for _ in range(2):
                self.g._event(db, self.row['erp_project'], self.row['invocation_id'],
                              'original_unsubmitted_rearmed', 'legacy-human-retry', {'fence': 1})
            db.execute('UPDATE governance_invocation SET dispatch_attempts=2 WHERE invocation_id=?',
                       (self.row['invocation_id'],))
        self.assertEqual(0, self.rearm()['dispatch_attempts'])
        claimed = self.g.claim_invocation(self.row['invocation_id'], 'fixture-first-run',
                                          gate=self.gate_value, db_path=self.path)
        self.assertEqual(1, claimed['dispatch_attempts'])

    def test_claimed_or_unexplained_counter_is_denied(self):
        self.block()
        with self.g._transaction(self.path) as db:
            db.execute('UPDATE governance_invocation SET dispatch_attempts=1 WHERE invocation_id=?',
                       (self.row['invocation_id'],))
        with self.assertRaisesRegex(ValueError, 'never claimed'):
            self.rearm()
        with self.g._transaction(self.path) as db:
            db.execute('UPDATE governance_invocation SET dispatch_attempts=0 WHERE invocation_id=?',
                       (self.row['invocation_id'],))
            self.g._event(db, self.row['erp_project'], self.row['invocation_id'],
                          'execution_claimed', 'executor', {'attempt': 1})
        with self.assertRaisesRegex(ValueError, 'never claimed'):
            self.rearm()
