"""Only newly composed prompt/profile-command boundaries; no services, DB or network."""
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
SRC = HERE / 'source/backend/open_webui' if (HERE / 'source').exists() else HERE.parent / 'runtime'
CONTRACTS = HERE / 'preserved-contracts' if (HERE / 'preserved-contracts').exists() else HERE.parent / 'preserved-contracts'
REPO = 'https://github.com/ABiT-Consulting/abit-ai-agents'
ENV = 'ABIT-DEV-LINUX'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prompt(packet, role='solution_architect', architecture=False):
    """Execute the actual prompt statements, excluding submission and imports."""
    tree = ast.parse((SRC / 'utils/agentic_executor.py').read_bytes())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DotAdapter')
    method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'submit')
    body = []
    recording = False
    for statement in method.body:
        if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'prompt' for t in statement.targets):
            recording = True
        if recording:
            if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'task' for t in statement.targets):
                break
            if isinstance(statement, ast.ImportFrom):
                assert statement.module == 'open_webui.utils.agentic_architecture_review'
                continue
            body.append(statement)
    assert len(body) == 4
    row = {'target_role': role, 'erp_project': 'PROJ-SYNTHETIC', 'erp_task': 'TASK-SYNTHETIC',
           'invocation_id': 'agov-synthetic-original', 'agent_id': 'agent-synthetic', 'context': '{}'}
    env = {'row': row, 'packet': packet, 'run_id': 'separate-automation-uuid', 'root': Path('/synthetic/evidence'),
           'implementation': {}, 'json': json, 'packet_digest': lambda p: p['content_sha256'],
           'digest': lambda v: hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest(),
           'architecture_context': lambda r, p: architecture}
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), '<actual-composed-prompt>', 'exec'), env)
    return env['prompt'], row


def packet():
    return {'content_sha256': 'a' * 64, 'project_id': 'PROJ-SYNTHETIC', 'task_id': 'TASK-SYNTHETIC',
            'binding': {'erp_user': 'ai-projects@example.invalid'}, 'srs': {'revision': '1', 'sha256': 'b' * 64},
            'project_backlog': {'context_sha256': 'c' * 64, 'snapshot_sha256': 'd' * 64}}


class Composition(unittest.TestCase):
    def test_actual_sa_prompt_keeps_fence_and_exact_distinct_metadata_identity(self):
        value = packet()
        text, row = prompt(value)
        marker = 'ARCHITECT PLAN METADATA IDENTITY: '
        metadata = json.JSONDecoder().raw_decode(text.split(marker, 1)[1])[0]
        self.assertEqual(metadata, {
            'project_id': row['erp_project'], 'srs_version': value['srs']['revision'],
            'srs_sha256': value['srs']['sha256'], 'source_url': 'https://erp.abit.ai/desk/project/' + row['erp_project'],
            'run_id': row['invocation_id'],
            'agent_binding': {'project_id': row['erp_project'], 'role': row['target_role'],
                              'abit_agent_id': row['agent_id'], 'erp_ai_user': value['binding']['erp_user']}})
        self.assertIn('OUTER AUTOMATION RUN (transport only): separate-automation-uuid', text)
        self.assertIn('single AGOV RESULT: line', text)
        self.assertIn('fenced plain-text block, preserving all JSON escapes exactly', text)
        self.assertEqual(text.count('GOVERNANCE WORK PACKET'), 1)
        self.assertEqual(text.count(marker), 1)
        self.assertNotEqual(metadata['run_id'], 'separate-automation-uuid')

    def test_actual_developer_qa_and_both_pm_phases_keep_fence_without_sa_metadata(self):
        for role, review, architecture in [('developer', False, False), ('qa', False, False),
                                            ('project_manager', False, True), ('project_manager', False, False),
                                            ('project_manager', True, False)]:
            with self.subTest(role=role, review=review, architecture=architecture):
                value = packet()
                if review:
                    value['qa_review'] = {'implementation_invocation_id': 'implementation',
                                          'implementation_agent_id': 'developer-agent'}
                text, _ = prompt(value, role, architecture)
                self.assertIn('fenced plain-text block, preserving all JSON escapes exactly', text)
                self.assertNotIn('ARCHITECT PLAN METADATA IDENTITY:', text)
                if architecture:
                    self.assertIn('PM ARCHITECTURE REVIEW PHASE:', text)
                    self.assertNotIn('PM DELEGATION PHASE:', text)
                elif role == 'project_manager' and not review:
                    self.assertIn('PM DELEGATION PHASE:', text)
                elif review:
                    self.assertIn('DEDICATED PM QA COORDINATION:', text)

    def test_profile_max_registered_argv_composes_with_preserved_shared_contract(self):
        profiles = load('composed_profiles', SRC / 'utils/agentic_project_profiles.py')
        helper = load('preserved_command_contract', CONTRACTS / 'developer_delivery.py')
        argv = ['\x01' * 500] * 30
        template = {'enabled': True, 'repository': REPO, 'environment': ENV,
                    'workspace_base': '/srv/abit-dev-linux/synthetic/workspaces', 'allowed_paths': ['src'],
                    'read_paths': ['src'], 'test_paths': ['src'], 'tests': {'unit': argv},
                    'test_acceptance_ids': {'unit': ['AC-SYNTHETIC']}, 'base_branch': 'main',
                    'base_commit': 'a' * 40, 'git_author': {'name': 'service', 'email': 'service@example.invalid'}}
        config = {'agentic_repository_gateway': {'projects': {}, 'templates': {'synthetic': template}}}
        profile, provenance = profiles.resolve_repository_profile(config, 'PROJ-SYNTHETIC', REPO, ENV)
        self.assertEqual(provenance['kind'], 'template')
        command = json.dumps(profile['tests']['unit'], sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        self.assertEqual(len(command), helper.MAX_REGISTERED_COMMAND_CHARS)
        self.assertEqual(helper.MAX_REGISTERED_COMMAND_CHARS, 90091)
        delivery = {'repository': REPO, 'base_commit': 'a' * 40, 'head_commit': 'b' * 40,
                    'pull_request_url': REPO + '/pull/1', 'changed_paths': ['src/example.py'],
                    'commit_evidence_id': 'commit', 'test_runs': [{'test_ids': ['AC-SYNTHETIC'],
                    'command': command, 'exit_code': 0, 'log_evidence_id': 'test-log'}]}
        self.assertEqual(helper.validate_delivery(delivery, REPO), [])
        delivery['test_runs'][0]['command'] += 'x'
        self.assertTrue(helper.validate_delivery(delivery, REPO))
        config['agentic_repository_gateway']['templates']['synthetic']['tests']['unit'].append('another')
        with self.assertRaises(profiles.ProfileUnavailable):
            profiles.resolve_repository_profile(config, 'PROJ-SYNTHETIC', REPO, ENV)
        schema = json.loads((CONTRACTS / 'schemas/governance_v1.schema.json').read_bytes())
        command_bounds = []
        def visit(value, chain=()):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == 'command' and isinstance(item, dict) and item.get('type') == 'string':
                        command_bounds.append(item.get('maxLength'))
                    visit(item, chain + (key,))
            elif isinstance(value, list):
                for item in value:
                    visit(item, chain)
        visit(schema)
        self.assertEqual(command_bounds, [90091])


if __name__ == '__main__':
    unittest.main(verbosity=2)
