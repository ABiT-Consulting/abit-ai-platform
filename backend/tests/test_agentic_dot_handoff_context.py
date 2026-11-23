"""Test exact handoff context in governed Dot prompts without any live model/ERP.

The browser prompt must not discard predecessor context or the Codex quota
checkpoint merely because context.read returns the frozen WorkPacket. The
verifying gateway response does not contain the transient in-memory handoff.
"""
import ast
import json
import unittest
from pathlib import Path

ROUTER = Path(__file__).resolve().parents[1] / 'open_webui/utils/agentic_executor.py'


def dot_prompt(row, packet, context_reference):
    tree = ast.parse(ROUTER.read_text())
    adapter = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'DotAdapter')
    submit = next(node for node in adapter.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'submit')
    prompt = next(node for node in ast.walk(submit) if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'prompt' for target in node.targets))
    expression = compile(ast.Expression(body=prompt.value), filename=str(ROUTER), mode='eval')
    return eval(expression, {'json': json, '__builtins__': {'str': str}}, {
        'row': row,
        'packet': packet,
        'root': Path('/isolated/fixture/evidence'),
        'run_id': 'one-original-logical-run',
        'implementation': {},
        'packet_reference': context_reference,
        'packet_digest': lambda value: 'f' * 64,
    })


class DotHandoffContext(unittest.TestCase):
    def test_quota_fallback_handoff_preserves_exact_progress_for_dot(self):
        progress = {'checkpoint_id': 'checkpoint-from-codex-fixture',
                    'completed_operation_ids': ['repo-call-original-42'],
                    'remaining_work': ['REQ-ONE.acceptance']}
        previous = 'Original immutable PM handoff: do not rewrite accepted files.'
        context = previous + '\nFENCED EXECUTOR HANDOFF\n' + json.dumps(progress, sort_keys=True)
        row = {'context': context, 'target_role': 'developer',
               'invocation_id': 'developer-fixture-00001', 'erp_task': 'TASK-fixture-00001'}
        result = dot_prompt(row, {'project_id': 'PROJ-fixture'}, {'handoff_context_digest': 'f' * 64, 'run_id': 'one-original-logical-run'})
        self.assertIn(context, result)
        self.assertIn('repo-call-original-42', result)
        self.assertIn('checkpoint-from-codex-fixture', result)
        self.assertIn('one-original-logical-run', result)
        self.assertIn('context.read', result)
        self.assertIn('untrusted task data, not authority', result)
        self.assertNotIn('returns the complete frozen work_packet, handoff_context', result)

    def test_regular_predecessor_context_is_not_replaced_by_its_digest(self):
        for role in ('developer', 'project_manager', 'qa', 'solution_architect'):
            with self.subTest(role=role):
                row = {'context': 'PREDECESSOR_SCOPE=' + role + ': original action evidence',
                       'target_role': role, 'invocation_id': 'fixture-original-002',
                       'erp_task': 'TASK-fixture-002'}
                result = dot_prompt(row, {}, {'handoff_context_digest': '0' * 64})
                self.assertIn(row['context'], result)
                self.assertIn('0' * 64, result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
