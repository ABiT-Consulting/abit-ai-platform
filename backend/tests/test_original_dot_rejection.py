"""Offline original failed-send receipt checks; never contacts a provider."""
from copy import deepcopy
import sqlite3
import unittest
import test_agentic_relay_scope as scope_tests


class OriginalDotRejection(scope_tests.RelayScope):
    async def original(self):
        _, _, envelope = await self.governed()
        module = self.load('abitdot_original_rejection')
        scope = envelope['governance_scope']['scope']
        proof = dict(ok=True, read_only=True, owner=envelope['owner_id'],
            account_id=scope['account_id'], job_id=scope['job_id'], dot_handoff=False,
            response='', conversation_url='https://chatgpt.com/dots/' + envelope['dot_id'],
            root_message_id='failed-original-root', reply_message_id=None,
            protocol='v4_outgoing_rejection', scope_receipt=self.provider(envelope)['scope_receipt'],
            rejection=dict(code='CHATGPT_MESSAGE_TOO_LONG', not_submitted=True, dialog=module.DIALOG),
            root_failure=dict(root_id='failed-original-root', original_envelope_matched=True,
                failed_to_send=True, failed_send_label=module.FAILED_LABEL, generating=False))
        return module, envelope, proof

    async def test_failed_root_is_negative_receipt_and_cannot_dispatch_again(self):
        module, envelope, proof = await self.original()
        with self.primary.ledger(self.path.parent / 'rejected.sqlite') as db:
            self.assertTrue(self.primary.claim(db, envelope)[0])
            before = db.execute('SELECT envelope_json FROM dot_primary_runs').fetchone()[0]
            result = self.primary.record_receipt(db, envelope, {
                **{k: envelope[k] for k in ('owner_id','automation_id','run_id','dot_id','idempotency_key')},
                'status':'rejected_before_submit','original_rejection':proof})
            self.assertFalse(result['retry_authorized'])
            self.assertIn('CHATGPT_MESSAGE_TOO_LONG', result['reason'])
            self.assertEqual(before, db.execute('SELECT envelope_json FROM dot_primary_runs').fetchone()[0])
            self.assertFalse(self.primary.claim(db, envelope)[0])
            self.assertEqual(db.execute('SELECT count(*) FROM dot_primary_runs').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM dot_primary_rejections').fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM dot_primary_rejections")

    async def test_global_dialog_or_wrong_root_does_not_prove_rejection(self):
        module, envelope, proof = await self.original()
        for mutate in (lambda p:p.pop('root_failure'),
                       lambda p:p['root_failure'].update(root_id='another-root'),
                       lambda p:p['root_failure'].update(failed_to_send=False),
                       lambda p:p.update(job_id='another-job'),
                       lambda p:p.update(reply_message_id='accepted-reply')):
            changed=deepcopy(proof); mutate(changed)
            with self.assertRaises(ValueError): module.verify_rejection(envelope,changed)

    async def test_future_empty_feed_requires_actual_capture_baseline(self):
        module, envelope, proof = await self.original()
        proof.pop('protocol');proof.pop('root_failure');proof['root_message_id']=None
        proof['rejection'].update(original_user_visible=False,feed_present=False,
                                  generating=False,users=0,assistants=0)
        with self.assertRaises(ValueError): module.verify_rejection(envelope,proof)
        proof['rejection_capture']=dict(send_attempted=True,baseline_empty=True,
            baseline_rejection_absent=True,accepted_observed=False)
        self.assertEqual(module.verify_rejection(envelope,proof)['job_id'],proof['job_id'])

    async def test_invalid_negative_receipt_keeps_original_unknown_and_envelope_unchanged(self):
        _, envelope, proof = await self.original()
        proof['root_failure']['failed_to_send'] = False
        with self.primary.ledger(self.path.parent / 'invalid.sqlite') as db:
            self.primary.claim(db,envelope)
            original=db.execute('SELECT envelope_json FROM dot_primary_runs').fetchone()[0]
            result=self.primary.record_receipt(db,envelope,{
                **{k:envelope[k] for k in ('owner_id','automation_id','run_id','dot_id','idempotency_key')},
                'status':'rejected_before_submit','original_rejection':proof})
            self.assertEqual(result['state'],'unknown')
            self.assertEqual(db.execute('SELECT envelope_json FROM dot_primary_runs').fetchone()[0],original)
            self.assertFalse(self.primary.claim(db,envelope)[0])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(OriginalDotRejection(n) for n in (
        'test_failed_root_is_negative_receipt_and_cannot_dispatch_again',
        'test_global_dialog_or_wrong_root_does_not_prove_rejection',
        'test_future_empty_feed_requires_actual_capture_baseline',
        'test_invalid_negative_receipt_keeps_original_unknown_and_envelope_unchanged'))
