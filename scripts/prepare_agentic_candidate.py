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


def maintenance_import(text):
    line = 'from open_webui.utils import agentic_maintenance as _agov_maintenance\n'
    if line in text:
        return text
    tree = ast.parse(text)
    before = 0
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            before = node.end_lineno
        elif isinstance(node, ast.ImportFrom) and node.module == '__future__':
            before = node.end_lineno
        else:
            break
    lines = text.splitlines(keepends=True)
    lines.insert(before, line)
    return ''.join(lines)


def admission_decorator(text, name, decorator, cls=None):
    text = maintenance_import(text)
    tree = ast.parse(text)
    body = tree.body
    if cls:
        classes = [n for n in body if isinstance(n, ast.ClassDef) and n.name == cls]
        if len(classes) != 1:
            raise ValueError('Expected one admission class ' + cls)
        body = classes[0].body
    nodes = [n for n in body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name]
    if len(nodes) != 1:
        raise ValueError('Expected one admission function ' + name)
    node = nodes[0]
    expression = '_agov_maintenance.' + decorator
    if any(ast.unparse(d) == expression for d in node.decorator_list):
        return text
    lines = text.splitlines(keepends=True)
    position = min([node.lineno, *[d.lineno for d in node.decorator_list]]) - 1
    lines.insert(position, ' ' * node.col_offset + '@' + expression + '\n')
    output = ''.join(lines)
    compile(output, name, 'exec')
    return output


def scheduler_admission(text):
    text = maintenance_import(text)
    if 'with _agov_maintenance.automation_batch():' in text:
        return text
    tree = ast.parse(text)
    tries = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Try)
        and any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == 'claim_due'
            for statement in n.body
            for c in ast.walk(statement)
        )
    ]
    if tries:
        span = min(n.end_lineno - n.lineno for n in tries)
        tries = [n for n in tries if n.end_lineno - n.lineno == span]
    if len(tries) != 1:
        raise ValueError('Expected one automation claim transaction block')
    node = tries[0]
    lines = text.splitlines(keepends=True)
    calls = [
        n
        for statement in node.body
        for n in ast.walk(statement)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == 'asyncio'
        and n.func.attr == 'create_task'
    ]
    if len(calls) != 1 or len(calls[0].args) != 1 or not isinstance(calls[0].args[0], ast.Call):
        raise ValueError('Expected one exact scheduled dispatch boundary')
    call = calls[0]
    inner = call.args[0]
    if (
        not isinstance(inner.func, ast.Name)
        or inner.func.id not in {'execute_automation', 'execute_scheduled_automation'}
        or inner.keywords
        or len(inner.args) not in {2, 3}
    ):
        raise ValueError('Scheduled dispatch signature changed')
    auto = ast.unparse(inner.args[1])
    app = ast.unparse(inner.args[0])
    defaults = 'a=' + auto
    arguments = app + ',a'
    if len(inner.args) == 3:
        defaults += ',r=' + ast.unparse(inner.args[2])
        arguments += ',r'
    original = ast.get_source_segment(text, call)
    replacement = (
        '_agov_maintenance.spawn_automation(lambda '
        + defaults
        + ': '
        + inner.func.id
        + '('
        + arguments
        + '), '
        + auto
        + '.id)'
    )
    # Preserve the surrounding live implementation and its scheduling metadata.
    segment = ''.join(lines[node.body[0].lineno - 1 : node.body[-1].end_lineno])
    if segment.count(original) != 1:
        raise ValueError('Scheduled dispatch source differs')
    segment = segment.replace(original, replacement, 1)
    indent = ' ' * node.body[0].col_offset
    wrapped = (
        indent
        + 'with _agov_maintenance.automation_batch():\n'
        + ''.join('    ' + line if line.strip() else line for line in segment.splitlines(keepends=True))
    )
    lines[node.body[0].lineno - 1 : node.body[-1].end_lineno] = [wrapped]
    output = ''.join(lines)
    compile(output, 'scheduler admission', 'exec')
    return output


def manual_admission(text):
    text = maintenance_import(text)
    original = 'asyncio.create_task(execute_automation(request.app, automation))'
    replacement = "try:\n        _agov_maintenance.spawn_automation(lambda: execute_automation(request.app, automation), automation.id)\n    except _agov_maintenance.MaintenanceDenied:\n        raise HTTPException(status_code=503, detail='Maintenance admission is closed; original work is preserved') from None"
    if replacement in text:
        return text
    if text.count(original) != 1:
        raise ValueError('Manual automation dispatch signature changed')
    # Admission happens synchronously before background Task creation.
    text = text.replace(original, replacement, 1)
    compile(text, 'manual admission', 'exec')
    return text


def pre_submission_receipts(text, *, primary=False):
    """Annotate only exact reviewed no-provider branches, never error text alone."""
    text = maintenance_import(text)
    tree = ast.parse(text)
    name = 'run_primary_dot' if primary else 'execute_automation'
    functions = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name]
    if len(functions) != 1:
        raise ValueError('Expected one pre-submission function ' + name)
    function = functions[0]
    branches = {}
    if primary:
        for node in ast.walk(function):
            if isinstance(node, ast.If) and ast.unparse(node.test) == 'folder is None':
                branches['dot_context_rejected-folder'] = node
            if (
                isinstance(node, ast.ExceptHandler)
                and node.type is not None
                and ast.unparse(node.type) == '(ValueError, PermissionError, KeyError)'
            ):
                branches['dot_context_rejected-context'] = node
        expected = {'dot_context_rejected-folder', 'dot_context_rejected-context'}
    else:
        for node in ast.walk(function):
            if not isinstance(node, ast.If):
                continue
            test = ast.unparse(node.test)
            if test in {'not user', 'not chat'}:
                branches['owner_not_found' if test == 'not user' else 'chat_not_created'] = node
            elif 'user.role' in test and any(
                isinstance(c, ast.Constant) and c.value == 'Owner no longer permitted to run automations'
                for c in ast.walk(node)
            ):
                branches['owner_not_authorized'] = node
        expected = {'owner_not_found', 'owner_not_authorized', 'chat_not_created'}
    if set(branches) != expected:
        raise ValueError('Pre-submission control branches changed')
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = []
    branch_calls = set()
    for reason, node in branches.items():
        calls = [
            c
            for c in ast.walk(node)
            if isinstance(c, ast.Call)
            and (
                isinstance(c.func, ast.Name)
                and c.func.id == '_record_run'
                or isinstance(c.func, ast.Attribute)
                and ast.unparse(c.func) == '_agov_maintenance.record_pre_submission'
            )
        ]
        if len(calls) != 1:
            raise ValueError('Expected one exact no-provider run receipt')
        call = calls[0]
        branch_calls.add(id(call))
        if isinstance(call.func, ast.Attribute):
            continue
        start = offsets[call.lineno - 1] + call.col_offset
        edits.append(
            (
                start,
                start + len('_record_run('),
                '_agov_maintenance.record_pre_submission(' + repr(reason.split('-')[0]) + ', _record_run, ',
            )
        )
    if primary:
        calls = [
            c
            for c in ast.walk(function)
            if isinstance(c, ast.Call)
            and id(c) not in branch_calls
            and (
                isinstance(c.func, ast.Name)
                and c.func.id == '_record_run'
                or isinstance(c.func, ast.Attribute)
                and ast.unparse(c.func) == '_agov_maintenance.record_dot_outcome'
            )
        ]
        if len(calls) != 1:
            raise ValueError('Expected one exact original Dot outcome run receipt')
        call = calls[0]
        if isinstance(call.func, ast.Name):
            start = offsets[call.lineno - 1] + call.col_offset
            edits.append(
                (start, start + len('_record_run('), '_agov_maintenance.record_dot_outcome(envelope, _record_run, ')
            )
    for start, end, value in sorted(edits, reverse=True):
        text = text[:start] + value + text[end:]
    compile(text, 'pre-submission receipts', 'exec')
    return text


def developer_docs(text):
    if all(name in text for name in ('ABITERPControl', 'ABITERPSignature', 'ABITGatewaySignature')):
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
    existing_route = next(
        n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == 'route_access'
    )
    prior = [
        n
        for n in existing_route.body
        if isinstance(n, ast.If) and '/api/v1/agentic/' in ast.get_source_segment(text, n.test)
    ]
    if len(prior) > 1:
        raise ValueError('Developer catalogue governance auth branch is ambiguous')
    if prior:
        current = text.splitlines(keepends=True)
        n = prior[0]
        text = ''.join(current[: n.lineno - 1]) + addition + ''.join(current[n.end_lineno :])
    else:
        anchor = '    if path == "/api/v1/knowledge/reindex":\n'
        if text.count(anchor) != 1:
            raise ValueError('Developer catalogue auth anchor changed')
        text = text.replace(anchor, addition + anchor, 1)
    anchor = '    scheme_specs = {\n'
    if text.count(anchor) != 1:
        raise ValueError('Developer catalogue scheme anchor changed')
    schemes = {
        'ABITERPControl': {
            'type': 'apiKey',
            'in': 'header',
            'name': 'X-ABIT-ERP-Control',
            'description': 'Protected ERP server credential; never put in browser/model prompts.',
        },
        'ABITERPSignature': {
            'type': 'apiKey',
            'in': 'header',
            'name': 'X-ABIT-Signature',
            'description': 'Requires exact timestamp, event identity and signed canonical ERP body.',
        },
        'ABITGatewaySignature': {
            'type': 'apiKey',
            'in': 'header',
            'name': 'X-ABIT-Gateway-Signature',
            'description': 'Internal authenticated MCP edge HMAC over timestamp and exact canonical scoped body.',
        },
    }
    text = text.replace(
        anchor,
        anchor + ''.join('        ' + repr(k) + ': ' + repr(v) + ',\n' for k, v in schemes.items() if k not in text),
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
            text = admission_decorator(text, 'execute_automation', 'guard_automation')
            text = admission_decorator(text, '_record_run', 'record_automation_receipt')
            text = scheduler_admission(text)
            text = pre_submission_receipts(text)
        else:
            from patch_live_agentic_run_access import apply

            text = apply(text)
            text = manual_admission(text)
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
            text = admission_decorator(
                text, 'review_one' if name == 'erp_pending_review_qa.py' else 'review_assigned_task', 'legacy_qa'
            )
            if name == 'erp_pending_review_qa.py':
                text = admission_decorator(text, 'claim', 'qa_claim', 'ReviewStore')
            write('qa-modules/' + name, text.encode(), path, 'surgical governed Task exclusion')
    for filename, names, cls in [
        ('erp_task_runtime.py', ['dispatch_event'], None),
        ('erp_task_resolution.py', ['intake', 'reset', 'advance'], 'Resolver'),
    ]:
        path = runtime / 'utils' / filename
        text = path.read_text()
        for name in names:
            text = admission_decorator(text, name, 'legacy_erp', cls)
        write('open_webui/utils/' + filename, text.encode(), path, 'surgical maintenance admission')
    path = runtime / 'utils/abitdot_primary_runner.py'
    text = pre_submission_receipts(path.read_text(), primary=True)
    write('open_webui/utils/abitdot_primary_runner.py', text.encode(), path, 'surgical original no-submit receipts')
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
