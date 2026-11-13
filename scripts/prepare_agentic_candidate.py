"""Stage a reviewable overlay. Never write to runtime, restart or touch live data.

Whole legacy router/model/main replacement is intentionally unavailable. The
manifest captures exact runtime hashes to recheck before an approved release.
"""

from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'backend/open_webui'


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def function_guard(text, name, guard):
    tree = ast.parse(text)
    matches = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    if len(matches) != 1:
        raise ValueError('Expected one function ' + name)
    node = matches[0]
    body = node.body
    first = (
        body[1]
        if isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
        else body[0]
    )
    lines = text.splitlines(keepends=True)
    if guard in ''.join(lines[node.lineno - 1 : node.end_lineno]):
        return text
    lines.insert(first.lineno - 1, guard)
    output = ''.join(lines)
    compile(output, name, 'exec')
    return output


def developer_docs(text):
    if 'ABITERPControl' in text and 'ABITERPSignature' in text:
        return text
    original = (SOURCE / 'utils/developer_api.py').read_text()
    route = next(n for n in ast.parse(original).body if isinstance(n, ast.FunctionDef) and n.name == 'route_access')
    block = next(
        n
        for n in route.body
        if isinstance(n, ast.If) and '/api/v1/agentic/' in ast.get_source_segment(original, n.test)
    )
    lines = original.splitlines(keepends=True)
    addition = ''.join(lines[block.lineno - 1 : block.end_lineno])
    anchor = '    if path == "/api/v1/knowledge/reindex":\n'
    if text.count(anchor) != 1:
        raise ValueError('Developer catalogue auth anchor changed')
    text = text.replace(anchor, addition + anchor, 1)
    anchor = '    scheme_specs = {\n'
    if text.count(anchor) != 1:
        raise ValueError('Developer catalogue scheme anchor changed')
    text = text.replace(
        anchor,
        anchor
        + '''        'ABITERPControl': {'type':'apiKey','in':'header','name':'X-ABIT-ERP-Control','description':'Protected ERP server credential; never put in browser/model prompts.'},
        'ABITERPSignature': {'type':'apiKey','in':'header','name':'X-ABIT-Signature','description':'Requires exact timestamp, event identity and signed canonical ERP body.'},
''',
        1,
    )
    compile(text, 'developer_api.py', 'exec')
    return text


def stage(runtime, out, qa_modules=None):
    runtime = runtime.resolve()
    out = out.resolve()
    if not runtime.is_dir() or out.exists():
        raise ValueError('Existing runtime and a NEW output directory are required')
    if out == runtime or runtime in out.parents or out in runtime.parents:
        raise ValueError('Candidate must be outside runtime')
    prerequisites = json.loads((ROOT / 'scripts/agentic_relay_prerequisites.json').read_text())
    for item in prerequisites:
        if fingerprint(runtime / item['path']) != item['from_live_sha256']:
            raise ValueError('Live prerequisite changed; review/rebase before staging: ' + item['path'])
    out.mkdir(parents=True, mode=0o750)
    manifest = []

    def write(relative, content, live, kind):
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if target.suffix == '.py':
            compile(content, str(target), 'exec')
        manifest.append(
            {
                'path': relative,
                'runtime_path': str(live),
                'runtime_sha256': fingerprint(live),
                'candidate_sha256': fingerprint(target),
                'operation': kind,
            }
        )

    for source in sorted((SOURCE / 'utils').glob('agentic_*.py')):
        relative = 'open_webui/utils/' + source.name
        write(relative, source.read_bytes(), runtime / 'utils' / source.name, 'reviewed governance module')
    for source in sorted((SOURCE / 'utils/agentic_contracts').rglob('*')):
        if not source.is_file() or '__pycache__' in source.parts:
            continue
        relative = 'open_webui/utils/agentic_contracts/' + str(source.relative_to(SOURCE / 'utils/agentic_contracts'))
        write(
            relative, source.read_bytes(), runtime / relative.removeprefix('open_webui/'), 'vendored immutable contract'
        )
    relative = 'routers/agentic_governance.py'
    write('open_webui/' + relative, (SOURCE / relative).read_bytes(), runtime / relative, 'reviewed governance router')
    for relative, name, guard in [
        ('routers/automations.py', '_get_owned_automation', None),
        (
            'utils/automations.py',
            'execute_automation',
            "    if (automation.meta or {}).get('governance_managed') is True:\n        return\n",
        ),
    ]:
        path = runtime / relative
        text = path.read_text()
        if guard:
            text = function_guard(text, name, guard)
        else:
            from patch_live_agentic_run_access import apply

            text = apply(text)
        write('open_webui/' + relative, text.encode(), path, 'surgical legacy entry guard')
    path = runtime / 'utils/developer_api.py'
    write(
        'open_webui/utils/developer_api.py',
        developer_docs(path.read_text()).encode(),
        path,
        'surgical API auth documentation',
    )
    # These are exact imported live prerequisites with scoped changes in this
    # source. Their current hashes must be checked again at approved deployment.
    for name in ('erp_dot_execution.py', 'abitdot_primary.py', 'abitdot_primary_relay.py'):
        path = runtime / 'utils' / name
        write(
            'open_webui/utils/' + name,
            (SOURCE / 'utils' / name).read_bytes(),
            path,
            'review legacy relay diff and exact baseline hash',
        )
    if qa_modules:
        for name, function in [('erp_pending_review_qa.py', 'matches_task'), ('erp_assigned_qa.py', 'task_current')]:
            path = qa_modules / name
            text = function_guard(
                path.read_text(), function, "    if task.get('custom_agentic_managed'):\n        return False\n"
            )
            write('qa-modules/' + name, text.encode(), path, 'surgical governed Task exclusion')
    (out / 'manifest.json').write_text(
        json.dumps(
            {
                'status': 'staged_only',
                'files': manifest,
                'forbidden': [
                    'whole backend overlay',
                    'main/model replacement',
                    'live database/config overwrite',
                    'active job cancellation',
                ],
                'release_prerequisites': [
                    'review and production approval',
                    'ERP installed/migrated and canary verified',
                    'scoped developer relay tools verified',
                    'consistent backups',
                    'current hash/active-job recheck',
                    'drain jobs',
                    'rollback and live QA',
                ],
            },
            indent=2,
        )
        + '\n'
    )
    return len(manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--qa-modules', type=Path)
    args = parser.parse_args()
    print('STAGED_ONLY files=' + str(stage(args.runtime, args.output, args.qa_modules)))
