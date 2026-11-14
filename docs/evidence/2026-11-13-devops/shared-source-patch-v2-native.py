"""READ-ONLY PROPOSAL: hash-pinned source inspection; candidates stay in memory."""

import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

BENCH = Path("/home/frappe/frappe-bench")
SITE = "erp.abit.ai"
PACKAGE = BENCH / "apps/abit_ai_assistant/abit_ai_assistant"
PINS = {
    "hooks.py": "3106abf4631ad5e048dc4e3938e81232ff3a4f6458c36838e898833b46abfdf2",
    "project_task_scheduler.py": "b8ab05817f3e4527b124157f401fda7b9bdcb77bf4e12eeaab99e80ee010ef0c",
    "scheduler.py": "be1e9817f4c2c568a11179888543e8cedc1e439eb6abca980bfdbe41bf94e6c5",
}
MAXIMUM = {
    "hooks.py": 65536,
    "project_task_scheduler.py": 262144,
    "scheduler.py": 262144,
}
OUTPUT_LIMIT = 49152
MARKER = "# ABIT AGOV additive integration proposal f064e6e0 (not applied)"
AST_SCHEMA = "ast-fields-v2-omit-none-and-empty-optional-fields"
UTF8_BOM = b"\xef\xbb\xbf"
COUNTS = {
    "fixed_files_read": 0,
    "writes_denied": 0,
    "network_denied": 0,
    "process_denied": 0,
}


class InspectionDenied(ValueError):
    def __init__(self, code, facts=None):
        super().__init__(code)
        self.facts = facts


def require(value, code):
    if not value:
        raise InspectionDenied(code)


def sha(value):
    return hashlib.sha256(
        value if isinstance(value, bytes) else value.encode()
    ).hexdigest()


def fingerprint(value):
    return sha(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )


def stable_ast(value):
    """Do not depend on version-specific ast.dump defaults or repr formatting."""
    if isinstance(value, ast.AST):
        fields = {}
        for name in value._fields:
            item = getattr(value, name, None)
            required_none = (
                isinstance(value, (ast.Constant, ast.MatchSingleton))
                and name == "value"
            )
            if item is None and not required_none:
                continue
            if isinstance(item, (list, tuple)) and not item:
                continue
            fields[name] = stable_ast(item)
        return {"node": type(value).__name__, "fields": fields}
    if isinstance(value, (list, tuple)):
        return [stable_ast(item) for item in value]
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, complex):
        return {"complex_hex": [value.real.hex(), value.imag.hex()]}
    if isinstance(value, float):
        return {"float_hex": value.hex()}
    if value is Ellipsis:
        return {"ellipsis": True}
    require(
        value is None or type(value) in {str, int, bool}, "AST_LITERAL_TYPE_UNREVIEWED"
    )
    return value


def structural(node):
    return fingerprint({"schema": AST_SCHEMA, "tree": stable_ast(node)})


def signature(fn):
    value = {"arguments": stable_ast(fn.args)}
    if fn.returns is not None:
        value["returns"] = stable_ast(fn.returns)
    if getattr(fn, "type_comment", None) is not None:
        value["type_comment"] = fn.type_comment
    if getattr(fn, "type_params", None):
        value["type_params"] = [stable_ast(item) for item in fn.type_params]
    return fingerprint({"schema": AST_SCHEMA, "signature": value})


def byte_syntax(raw, filename, phase):
    """Parse the exact bytes as Python import does; never expose SyntaxError.text."""
    try:
        return ast.parse(raw, filename=filename)
    except SyntaxError as error:
        message = str(error.msg or "").lower()
        reason = "UNCLASSIFIED_SYNTAX"
        for fragments, code in [
            (("encoding",), "ENCODING_DECLARATION"),
            (("non-printable character",), "NONPRINTABLE_CHARACTER"),
            (("invalid character",), "INVALID_CHARACTER"),
            (("unterminated",), "UNTERMINATED_STRING"),
            (("indent", "tabs and spaces"), "INDENTATION"),
            (("never closed", "unmatched"), "DELIMITER"),
            (("invalid syntax",), "INVALID_SYNTAX"),
        ]:
            if any(fragment in message for fragment in fragments):
                reason = code
                break
        line = (
            error.lineno
            if type(error.lineno) is int and 0 < error.lineno <= 100000
            else 0
        )
        raise InspectionDenied(
            phase + "_BYTES_SYNTAX_FAILED",
            {"syntax_reason": reason, "syntax_line": line},
        ) from None


def source_bytes(raw, name):
    require(
        name in PINS and isinstance(raw, bytes) and 0 < len(raw) <= MAXIMUM[name],
        "FIXED_FILE_SIZE_BOUND",
    )
    bom = UTF8_BOM if raw.startswith(UTF8_BOM) else b""
    body = raw[len(bom) :]
    # Reject unreviewed codec declarations before a parser could load a codec.
    for line in body.split(b"\n", 2)[:2]:
        cookie = re.match(rb"^[ \t\f]*\#.*?coding[:=][ \t]*([-\w.]+)", line)
        if cookie:
            encoding = cookie.group(1).lower().replace(b"_", b"-")
            require(encoding in {b"utf-8", b"utf8"}, "SOURCE_ENCODING_UNREVIEWED")
    if b"\r" in body:
        require(
            body.count(b"\r") == body.count(b"\r\n") == body.count(b"\n"),
            "SOURCE_MIXED_OR_BARE_CR_NEWLINES",
        )
        newline = "\r\n"
    else:
        newline = "\n"
    require(body.endswith(b"\n"), "SOURCE_TERMINAL_NEWLINE_UNREVIEWED")
    tree = byte_syntax(raw, "abit_ai_assistant/" + name, "ORIGINAL")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise InspectionDenied("SOURCE_ENCODING_UNREVIEWED") from None
    return text, tree, bom, newline


HOOK_ADDITIONS = {
    "Project": {
        "after_insert": ["abit_ai_assistant.project_srs.record_project_srs_revision"],
        "validate": [
            "abit_ai_assistant.agentic_permissions.guard_project_write",
            "abit_ai_assistant.project_srs.validate_project_srs",
        ],
        "on_update": [
            "abit_ai_assistant.project_srs.record_project_srs_revision",
            "abit_ai_assistant.agentic_control.capture_project",
        ],
    },
    "File": {
        "before_insert": ["abit_ai_assistant.agentic_permissions.guard_file_write"],
        "validate": ["abit_ai_assistant.agentic_permissions.guard_file_write"],
        "on_trash": ["abit_ai_assistant.agentic_permissions.guard_file_write"],
        "after_insert": ["abit_ai_assistant.project_srs.record_srs_file_insert"],
        "on_update": ["abit_ai_assistant.project_srs.record_srs_file_insert"],
        "after_delete": ["abit_ai_assistant.project_srs.record_srs_file_insert"],
    },
    "Task": {
        "before_insert": ["abit_ai_assistant.agentic_permissions.guard_task_write"],
        "validate": ["abit_ai_assistant.agentic_permissions.guard_task_write"],
        "after_insert": ["abit_ai_assistant.agentic_control.capture_task"],
        "on_update": ["abit_ai_assistant.agentic_control.capture_task"],
    },
    "ToDo": {
        "before_insert": ["abit_ai_assistant.agentic_permissions.guard_assignment"],
        "validate": ["abit_ai_assistant.agentic_permissions.guard_assignment"],
        "after_insert": ["abit_ai_assistant.agentic_control.capture_assignment"],
        "on_update": ["abit_ai_assistant.agentic_control.capture_assignment"],
    },
}
MIGRATE = [
    "abit_ai_assistant.project_srs.ensure_project_srs_fields",
    "abit_ai_assistant.agentic_control.ensure_control_fields",
]
INSTALL = [
    "abit_ai_assistant.project_srs.ensure_project_srs_fields",
    "abit_ai_assistant.agentic_control.ensure_control_fields",
]
ASSET = "/assets/abit_ai_assistant/js/agentic_governance.js"
FILE_MIXIN = "abit_ai_assistant.agentic_file.GovernedEvidenceFile"
GUARDS_FIRST = {"before_insert", "validate", "on_trash"}


def sequence(value, *, asset=False):
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    require(
        isinstance(values, (list, tuple)) and len(values) <= 100, "HOOK_SEQUENCE_SHAPE"
    )
    require(
        all(isinstance(item, str) and 0 < len(item) <= 500 for item in values),
        "HOOK_VALUE_SHAPE",
    )
    if not asset:
        require(
            all(
                re.fullmatch(
                    r"(?:abit_ai_assistant|frappe|erpnext|hrms)\.[A-Za-z_][A-Za-z0-9_.]{0,240}",
                    item,
                )
                for item in values
            ),
            "HOOK_CALL_TARGET_UNREVIEWED",
        )
    return list(values)


def merge_sequence(before, additions, first=False):
    new = [value for value in additions if value not in before]
    return new + before if first else before + new


def hooks_plan(text, tree, newline):
    values = {}
    assignments = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        require(
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name),
            "HOOKS_REQUIRE_STATIC_LITERAL_ASSIGNMENTS",
        )
        name = node.targets[0].id
        require(not name.startswith("_agov"), "HOOK_RESERVED_GLOBAL_CONFLICT")
        try:
            require(
                not any(
                    isinstance(item, (ast.Call, ast.Name, ast.Attribute))
                    for item in ast.walk(node.value)
                ),
                "HOOKS_DYNAMIC_VALUE_UNREVIEWED",
            )
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            raise InspectionDenied("HOOKS_DYNAMIC_VALUE_UNREVIEWED") from None
        lines = assignments.setdefault(name, [])
        lines.append(node.lineno)
        if len(lines) > 1:
            require(
                name == "after_migrate" and lines == [12, 47],
                "HOOK_DUPLICATE_NOT_EXACT_OBSERVED_AFTER_MIGRATE",
            )
            sequence(values[name])
            sequence(value)
        # Python's last simple literal assignment is its effective binding.
        values[name] = value
    require(MARKER not in text, "INTEGRATION_MARKER_ALREADY_PRESENT")
    extensions = values.get("extend_doctype_class") or {}
    overrides = values.get("override_doctype_class") or {}
    require(
        isinstance(extensions, dict) and isinstance(overrides, dict),
        "FILE_HOOK_MAP_SHAPE",
    )
    require(
        not extensions.get("File") and not overrides.get("File"),
        "EXISTING_FILE_CLASS_REQUIRES_SEPARATE_MRO_REVIEW",
    )
    events = values.get("doc_events") or {}
    require(isinstance(events, dict), "DOC_EVENTS_MAP_SHAPE")
    facts = []
    for doctype, changes in HOOK_ADDITIONS.items():
        current = events.get(doctype) or {}
        require(isinstance(current, dict), "DOC_EVENT_CLASS_SHAPE")
        for event, additions in changes.items():
            before = sequence(current.get(event))
            require(
                not set(before).intersection(additions),
                "GOVERNANCE_HOOK_ALREADY_PRESENT",
            )
            after = merge_sequence(before, additions, event in GUARDS_FIRST)
            facts.append(
                {
                    "doctype": doctype,
                    "event": event,
                    "existing_count": len(before),
                    "existing_order_sha256": fingerprint(before),
                    "candidate_order_sha256": fingerprint(after),
                    "existing_relative_order_preserved": True,
                    "added_targets": additions,
                    "new_guards_precede_existing": event in GUARDS_FIRST,
                }
            )
    other = []
    for key, additions, asset in [
        ("app_include_js", [ASSET], True),
        ("after_migrate", MIGRATE, False),
        ("after_install", INSTALL, False),
    ]:
        before = sequence(values.get(key), asset=asset)
        require(
            not set(before).intersection(additions),
            "GOVERNANCE_GLOBAL_HOOK_ALREADY_PRESENT",
        )
        other.append(
            {
                "hook": key,
                "existing_count": len(before),
                "existing_order_sha256": fingerprint(before),
                "candidate_order_sha256": fingerprint(
                    merge_sequence(before, additions)
                ),
                "existing_relative_order_preserved": True,
                "added_targets": additions,
            }
        )
    footer = (
        "\n"
        + MARKER
        + "\n"
        + """def _agov_append_preserving_existing(value, additions, first=False):
    current = [] if value is None else [value] if isinstance(value, str) else list(value)
    missing = [item for item in additions if item not in current]
    return missing + current if first else current + missing

app_include_js = _agov_append_preserving_existing(globals().get('app_include_js'), ASSET_VALUE)
after_migrate = _agov_append_preserving_existing(globals().get('after_migrate'), MIGRATE_VALUE)
after_install = _agov_append_preserving_existing(globals().get('after_install'), INSTALL_VALUE)
doc_events = dict(globals().get('doc_events') or {})
for _agov_doctype, _agov_additions in EVENT_VALUE.items():
    _agov_existing_events = dict(doc_events.get(_agov_doctype) or {})
    for _agov_event, _agov_targets in _agov_additions.items():
        _agov_existing_events[_agov_event] = _agov_append_preserving_existing(
            _agov_existing_events.get(_agov_event), _agov_targets,
            first=_agov_event in {'before_insert', 'validate', 'on_trash'})
    doc_events[_agov_doctype] = _agov_existing_events
extend_doctype_class = dict(globals().get('extend_doctype_class') or {})
extend_doctype_class['File'] = _agov_append_preserving_existing(
    extend_doctype_class.get('File'), MIXIN_VALUE)
"""
    )
    for key, value in [
        ("ASSET_VALUE", [ASSET]),
        ("MIGRATE_VALUE", MIGRATE),
        ("INSTALL_VALUE", INSTALL),
        ("EVENT_VALUE", HOOK_ADDITIONS),
        ("MIXIN_VALUE", [FILE_MIXIN]),
    ]:
        footer = footer.replace(key, repr(value))
    return text + footer.replace("\n", newline), {
        "literal_hook_module": True,
        "existing_nonexported_globals": len(values),
        "doc_event_orders": facts,
        "global_hook_orders": other,
        "existing_File_override_or_extension": False,
        "added_File_mixin": FILE_MIXIN,
        "observed_duplicate_after_migrate": assignments.get("after_migrate")
        == [12, 47],
        "after_migrate_final_assignment_semantics": True,
    }


EXPECTED_FUNCTIONS = {
    "_ensure_ai_task_for_reference": {
        "arguments_sha256": "b13c5af9b35b04f277c278d9fca104185bbbbbc61ee7d176c4a136074c6cfec9",
        "signature_sha256": "d845195660212e1eab23dafaa7343456a796573b09ca9a5c25c3b51f5ddb8d49",
        "prefix_sha256": "d8c802d9c53a94ab6aa64ea4a9e1aeddff54423a3bc7896b9cd40c11c8e80e69",
        "decorators_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    "run_assigned_ai_task": {
        "arguments_sha256": "7368a0560f0a002ceaa4e69bb22bd795efc359e1de373997ca00d6ace592c4a6",
        "signature_sha256": "8b700d966296a8e6b2ad30a27e3f62386a21c7cb7521c9edb2479cb913f1b9a8",
        "prefix_sha256": "ad49b7943efd6d5b5eab6c1271d09ba8b5981e4b4eca9704d6777117e02e9d51",
        "decorators_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    "run_project": {
        "arguments_sha256": "03bc6ef1853422c2aa60230768380f5eef04148e809d3227c3c62f6cab85f0eb",
        "signature_sha256": "eaf5ee17a5de05784add9d8b2642f804873c23d8059cf67849156806e3fb1e06",
        "prefix_sha256": "8ee4226201efcfc98d910325e7495c7cb9486c5933934655ed83aa05665822e8",
        "decorators_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
}  # Version-neutral reviewed-main AST fingerprints.


def function(tree, name):
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    require(len(matches) == 1, "LEGACY_FUNCTION_MISSING_OR_DUPLICATE")
    fn = matches[0]
    require(
        not fn.decorator_list
        or all(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "frappe"
            and d.func.attr == "whitelist"
            for d in fn.decorator_list
        ),
        "LEGACY_DECORATOR_UNREVIEWED",
    )
    require(
        all(
            not (isinstance(node, ast.Name) and node.id == "managed_reference")
            for node in ast.walk(fn)
        ),
        "LEGACY_GUARD_ALREADY_PRESENT_OR_SHADOWED",
    )
    return fn


def anchor(fn, kind):
    if kind == "run_project":
        body = fn.body
        if (
            isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        require(bool(body), "LEGACY_FUNCTION_EMPTY")
        return body[0], "before", ast.Module(body=[], type_ignores=[])
    candidates = []
    for node in ast.walk(fn):
        if (
            not isinstance(node, ast.Assign)
            or len(node.targets) != 1
            or not isinstance(node.targets[0], ast.Name)
        ):
            continue
        call = node.value
        if (
            kind == "_ensure_ai_task_for_reference"
            and node.targets[0].id == "agent"
            and isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_user_agent"
            and len(call.args) == 1
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "user"
            and not call.keywords
        ):
            candidates.append(node)
        if (
            kind == "run_assigned_ai_task"
            and node.targets[0].id == "initial_doc"
            and isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "frappe"
            and call.func.attr == "get_doc"
            and len(call.args) == 2
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == "AI Task"
            and isinstance(call.args[1], ast.Name)
            and call.args[1].id == "task_name"
            and not call.keywords
        ):
            candidates.append(node)
    require(len(candidates) == 1, "LEGACY_INSERTION_ANCHOR_UNREVIEWED")
    point = candidates[0]
    if kind == "_ensure_ai_task_for_reference":
        require(point in fn.body, "LEGACY_REFERENCE_GUARD_NOT_TOP_LEVEL")
        prefix = ast.Module(body=fn.body[: fn.body.index(point)], type_ignores=[])
        return point, "before", prefix
    tries = [
        node for node in fn.body if isinstance(node, ast.Try) and point in node.body
    ]
    require(
        len(tries) == 1 and tries[0].body[0] is point,
        "LEGACY_DISPATCH_ANCHOR_NOT_FIRST_TRY_STATEMENT",
    )
    prefix = ast.Module(
        body=fn.body[: fn.body.index(tries[0])] + [point], type_ignores=[]
    )
    return point, "after", prefix


GUARDS = {
    "run_project": "from abit_ai_assistant.agentic_control import managed_reference\nif managed_reference('Project',project_name):\n    return {'project':project_name,'status':'governance_managed','message':'Use signed Agentic Governance project controls.'}\n",
    "_ensure_ai_task_for_reference": "from abit_ai_assistant.agentic_control import managed_reference\nif managed_reference(reference_type,reference_name):\n    return ''\n",
    "run_assigned_ai_task": "from abit_ai_assistant.agentic_control import managed_reference\nif managed_reference(initial_doc.get('reference_doctype'),initial_doc.get('reference_name')):\n    return {'status':'governance_managed','task':task_name,'reason':'Legacy execution blocked; use the existing governed invocation.'}\n",
}


def legacy_plan(text, names, tree, newline):
    rows = text.splitlines(keepends=True)
    insertions = []
    facts = []
    for name in names:
        fn = function(tree, name)
        point, where, prefix = anchor(fn, name)
        reviewed = EXPECTED_FUNCTIONS[name]
        observed = {
            "function": name,
            "arguments_sha256": structural(fn.args),
            "signature_sha256": signature(fn),
            "decorators_sha256": fingerprint(
                [structural(node) for node in fn.decorator_list]
            ),
            "prefix_sha256": structural(prefix),
            "function_sha256": structural(fn),
            "anchor_sha256": structural(point),
            "anchor_line": point.lineno,
        }
        for field, code in [
            ("arguments_sha256", "LEGACY_SIGNATURE_CHANGED"),
            ("signature_sha256", "LEGACY_SIGNATURE_CHANGED"),
            ("decorators_sha256", "LEGACY_DECORATORS_CHANGED"),
            ("prefix_sha256", "LEGACY_PREFLIGHT_PREFIX_CHANGED"),
        ]:
            if observed[field] != reviewed[field]:
                raise InspectionDenied(code, observed)
        line = point.lineno - 1 if where == "before" else point.end_lineno
        require(
            point.col_offset in {4, 8}
            and "\t" not in rows[point.lineno - 1][: point.col_offset],
            "LEGACY_INDENTATION_UNREVIEWED",
        )
        require(
            point.lineno <= point.end_lineno <= len(rows),
            "LEGACY_ANCHOR_SPAN_UNREVIEWED",
        )
        code = "".join(
            " " * point.col_offset + value if value.strip() else value
            for value in GUARDS[name].splitlines(keepends=True)
        )
        code = code.replace("\n", newline)
        insertions.append((line, code))
        facts.append(
            {
                "function": name,
                "arguments_sha256": structural(fn.args),
                "signature_sha256": signature(fn),
                "prefix_sha256": structural(prefix),
                "function_sha256": structural(fn),
                "anchor_sha256": structural(point),
                "anchor_line": point.lineno,
                "insertion": where,
                "new_guard_only": True,
            }
        )
    for line, code in sorted(insertions, reverse=True):
        rows.insert(line, code)
    return "".join(rows), {"functions": facts}


def prepare_bytes(name, raw):
    text, tree, bom, newline = source_bytes(raw, name)
    candidate, facts = (
        hooks_plan(text, tree, newline)
        if name == "hooks.py"
        else legacy_plan(
            text,
            (
                ["run_project"]
                if name == "project_task_scheduler.py"
                else ["_ensure_ai_task_for_reference", "run_assigned_ai_task"]
            ),
            tree,
            newline,
        )
    )
    candidate_raw = bom + candidate.encode("utf-8")
    byte_syntax(candidate_raw, "abit_ai_assistant/" + name, "CANDIDATE")
    diff = "".join(
        difflib.unified_diff(
            text.splitlines(keepends=True),
            candidate.splitlines(keepends=True),
            fromfile="a/abit_ai_assistant/" + name,
            tofile="b/abit_ai_assistant/" + name,
            n=0,
        )
    )
    require(
        not any(line.startswith("-") for line in diff.splitlines()[2:]),
        "PATCH_REMOVAL_OR_EXISTING_VALUE_DISCLOSURE_DENIED",
    )
    require(len(diff.encode()) <= 16384, "ADDITIVE_DIFF_BOUND")
    return {
        "path": "abit_ai_assistant/" + name,
        "original_sha256": sha(raw),
        "candidate_sha256": sha(candidate_raw),
        "original_bytes": len(raw),
        "candidate_bytes": len(candidate_raw),
        "encoding": "utf-8",
        "UTF8_BOM_preserved": bool(bom),
        "newline_preserved": "CRLF" if newline == "\r\n" else "LF",
        "ast_fingerprint_schema": AST_SCHEMA,
        "unified_diff_context": 0,
        "unified_diff": diff,
        "facts": facts,
        "preserves_every_original_byte": True,
        "candidate_in_memory_only": True,
    }


def prepare_inputs(values):
    require(set(values) == set(PINS), "FIXED_INPUT_SET_CHANGED")
    plans = []
    for name in PINS:
        try:
            plans.append(prepare_bytes(name, values[name]))
        except InspectionDenied as error:
            error.phase = name + ":AST"
            raise
    return plans


def audit(event, args):
    if event == "open":
        path, mode, flags = args
        write = (
            isinstance(mode, str)
            and any(char in mode for char in "wax+")
            or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        )
        if write:
            COUNTS["writes_denied"] += 1
            raise InspectionDenied("FILESYSTEM_WRITE_DENIED")
        if not isinstance(path, int):
            require(
                str(path) in {str(PACKAGE / name) for name in PINS},
                "UNALLOWLISTED_FILE_READ_DENIED",
            )
    if event.startswith("socket."):
        COUNTS["network_denied"] += 1
        raise InspectionDenied("NETWORK_DENIED")
    if event.startswith(("subprocess.", "os.exec", "os.spawn", "os.system")):
        COUNTS["process_denied"] += 1
        raise InspectionDenied("CHILD_PROCESS_DENIED")
    if event in {
        "os.remove",
        "os.rename",
        "os.mkdir",
        "os.rmdir",
        "os.link",
        "os.symlink",
        "os.chmod",
        "os.chown",
        "os.truncate",
        "os.utime",
        "os.putenv",
        "os.unsetenv",
    }:
        COUNTS["writes_denied"] += 1
        raise InspectionDenied("FILESYSTEM_OR_ENV_MUTATION_DENIED")


def read_fixed(name):
    path = PACKAGE / name
    require(path.resolve(strict=True) == path, "FIXED_SOURCE_SYMLINK_DENIED")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        require(
            stat.S_ISREG(before.st_mode) and 0 < before.st_size <= MAXIMUM[name],
            "FIXED_SOURCE_NOT_BOUNDED_REGULAR",
        )
        raw = handle.read(MAXIMUM[name] + 1)
        after = os.fstat(handle.fileno())
    require(
        len(raw) == before.st_size
        and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
        "FIXED_SOURCE_CHANGED_DURING_READ",
    )
    require(sha(raw) == PINS[name], "PRODUCTION_SOURCE_PIN_CHANGED")
    COUNTS["fixed_files_read"] += 1
    return raw


def main():
    phase = "identity"
    result = {
        "schema_version": 1,
        "purpose": "erp-three-file-memory-patch-proposal-v2",
        "site": SITE,
        "ok": False,
        "applied": False,
        "source_or_config_writes": False,
        "sql_queries": 0,
        "model_requests": 0,
    }
    try:
        require(
            (BENCH / "sites" / SITE).resolve(strict=True) == BENCH / "sites" / SITE
            and (BENCH / "sites" / SITE).is_dir(),
            "FIXED_SITE_DIRECTORY_CHANGED",
        )
        sys.addaudithook(audit)
        values = {}
        for name in PINS:
            phase = name
            values[name] = read_fixed(name)
        phase = "all_files:AST"
        result["files"] = prepare_inputs(values)
        result["ok"] = True
    except BaseException as error:
        result["error_code"] = (
            str(error)
            if isinstance(error, InspectionDenied)
            else "SOURCE_AST_INSPECTION_UNAVAILABLE"
        )
        result["error_type"] = (
            type(error).__name__
            if type(error).__name__
            in {
                "InspectionDenied",
                "SyntaxError",
                "UnicodeDecodeError",
                "FileNotFoundError",
                "PermissionError",
            }
            else "Unavailable"
        )
        result["failed_phase"] = (
            getattr(error, "phase", phase)
            if isinstance(error, InspectionDenied)
            else phase
        )
        if isinstance(error, InspectionDenied) and error.facts is not None:
            result["observed_ast_fingerprints"] = error.facts
    result["safety_counters"] = COUNTS
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > OUTPUT_LIMIT:
        result["ok"] = False
        encoded = json.dumps(
            {
                "schema_version": 1,
                "purpose": result["purpose"],
                "site": SITE,
                "ok": False,
                "error_code": "INSPECTION_OUTPUT_BOUND",
                "applied": False,
            }
        )
    sys.stdout.write(encoded + "\n")
    raise SystemExit(0 if result["ok"] else 65)


if __name__ == "__main__":
    main()
