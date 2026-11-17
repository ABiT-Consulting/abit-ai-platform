"""Prepare the explicit current Slack HTML registration support for verification only."""
from pathlib import Path
import ast
import difflib
import hashlib
import json

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = HERE / "api-verification-current"
sha = lambda raw: hashlib.sha256(raw).hexdigest()
assert not OUT.exists()
files = ["scripts/check_api_contract.py", "scripts/tests/test_api_contract.py", "docs/api-contract-policy.json"]
before = {name: (REPO / name).read_bytes() for name in files}
checker = before[files[0]].decode()
old_constant = 'UI_MODULE = "open_webui.utils.developer_ui"\n'
new_constant = '''UI_REGISTRATIONS = {
    "open_webui.utils.developer_ui.register_developer_ui": "open_webui.utils.developer_ui",
    "open_webui.utils.slack_ui.register_slack_ui": "open_webui.utils.slack_ui",
}
'''
old_block = '''        for node in ast.walk(main.tree):
            if isinstance(node, ast.Call) and main.ref(node.func) == UI_MODULE + ".register_developer_ui":
                if not node.args or main.ref(node.args[0]) != MAIN:
                    failure(main, node, "developer UI registration must target the main app")
                else:
                    helpers.append(UI_MODULE + ".app")
                    objects[UI_MODULE + ".app"] = ("", True)
'''
new_block = '''        for node in ast.walk(main.tree):
            helper = UI_REGISTRATIONS.get(main.ref(node.func)) if isinstance(node, ast.Call) else None
            if helper:
                if not node.args or main.ref(node.args[0]) != MAIN:
                    failure(main, node, "optional UI registration must target the main app")
                else:
                    helpers.append(helper + ".app")
                    objects[helper + ".app"] = ("", True)
'''
assert checker.count(old_constant) == checker.count(old_block) == 1
amended_checker = checker.replace(old_constant, new_constant, 1).replace(old_block, new_block, 1)
assert amended_checker.replace(new_constant, old_constant, 1).replace(new_block, old_block, 1) == checker

tests = before[files[1]].decode()
addition = '''    def test_optional_slack_html_routes_need_precise_policy(self):
        paths = ("/dot/slack", "/dot/slack/", "/dot/slack/authorize", "/dot/slack/authorize/")
        self.write("backend/open_webui/utils/slack_ui.py",
            "def register_slack_ui(app, folder):\\n"
            "    for path in " + repr(paths) + ":\\n"
            "        app.add_api_route(path, lambda: '', methods=['GET', 'HEAD'], include_in_schema=False)\\n")
        self.append("backend/open_webui/main.py",
            "\\nfrom open_webui.utils.slack_ui import register_slack_ui\\nregister_slack_ui(app, 'frontend')\\n")
        operations, errors = guard.check(self.root)
        self.assertEqual(sum("undocumented HTTP operation" in error for error in errors), 8)
        self.assertEqual(sum(not operation.visible for operation in operations), 8)
        entries = [{"source": "backend/open_webui/utils/slack_ui.py", "path": path,
                    "methods": ["GET", "HEAD"],
                    "reason": "Optional standalone HTML navigation shell with separately covered API handlers."}
                   for path in paths]
        self.policy(entries)
        self.assertEqual(self.errors(), "")
        entries[0]["methods"].append("POST")
        self.policy(entries)
        self.assertIn("stale API visibility exception", self.errors())

    def test_optional_slack_helper_rejects_another_application(self):
        self.write("backend/open_webui/utils/slack_ui.py",
            "def register_slack_ui(app, folder):\\n"
            "    app.add_api_route('/dot/slack', lambda: '', methods=['GET'], include_in_schema=False)\\n")
        self.append("backend/open_webui/main.py",
            "\\nfrom open_webui.utils.slack_ui import register_slack_ui\\n"
            "other = FastAPI()\\nregister_slack_ui(other, 'frontend')\\n")
        self.assertIn("optional UI registration must target the main app", self.errors())

'''
anchor = '\n\nif __name__ == "__main__":\n'
assert tests.count(anchor) == 1
amended_tests = tests.replace(anchor, "\n\n" + addition + 'if __name__ == "__main__":\n', 1)
assert amended_tests.replace("\n\n" + addition + 'if __name__ == "__main__":\n', anchor, 1) == tests

policy = json.loads(before[files[2]])
paths = ["/dot/slack", "/dot/slack/", "/dot/slack/authorize", "/dot/slack/authorize/"]
entries = [{
    "source": "backend/open_webui/utils/slack_ui.py",
    "path": path,
    "methods": ["GET", "HEAD"],
    "reason": "Optional standalone Slack HTML navigation shell returned as HTMLResponse. Slack API and OAuth handlers are registered separately and remain covered by the source guard."
} for path in paths]
assert len(policy["hidden_operations"]) == 2
policy["hidden_operations"].extend(entries)
after = {
    files[0]: amended_checker.encode(),
    files[1]: amended_tests.encode(),
    files[2]: (json.dumps(policy, indent=2) + "\n").encode(),
}
restored = json.loads(after[files[2]])
del restored["hidden_operations"][-4:]
assert restored == json.loads(before[files[2]])
for name in files[:2]:
    compile(after[name], name, "exec")
rows = []
for name in files:
    for directory, raw in [("source-before", before[name]), ("candidate", after[name])]:
        path = OUT / directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as target:
            target.write(raw)
    diff = "".join(difflib.unified_diff(before[name].decode().splitlines(True),
                                      after[name].decode().splitlines(True),
                                      fromfile="before/" + name, tofile="candidate/" + name))
    diff_path = OUT / "diff" / (name + ".diff")
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    with diff_path.open("x") as target:
        target.write(diff)
    rows.append({"path": name, "before_sha256": sha(before[name]), "after_sha256": sha(after[name]),
                 "before_bytes": len(before[name]), "after_bytes": len(after[name]),
                 "diff_sha256": sha(diff.encode())})

# Ground registration support in the retained current source, without importing it.
slack_path = HERE / "snapshot/open_webui/utils/slack_ui.py"
main_path = HERE / "snapshot/open_webui/main.py"
slack = ast.parse(slack_path.read_bytes())
main = ast.parse(main_path.read_bytes())
function = next(node for node in slack.body if isinstance(node, ast.FunctionDef) and node.name == "register_slack_ui")
assert function.args.args[0].arg == "app"
loop = next(node for node in ast.walk(function) if isinstance(node, ast.For))
assert list(ast.literal_eval(loop.iter)) == paths
call = next(node for node in ast.walk(loop) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_api_route")
assert isinstance(call.func.value, ast.Name) and call.func.value.id == "app"
keywords = {item.arg: item.value for item in call.keywords}
assert ast.literal_eval(keywords["methods"]) == ["GET", "HEAD"]
assert ast.literal_eval(keywords["include_in_schema"]) is False
assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
           and node.func.id == "register_slack_ui" and node.args
           and isinstance(node.args[0], ast.Name) and node.args[0].id == "app" for node in ast.walk(main))
record = {
    "schema_version": 1,
    "state": "CURRENT_SOURCE_VERIFICATION_OVERLAY_PREPARED",
    "files": rows,
    "checker_exact_inverse": True,
    "tests_exact_inverse": True,
    "policy_inverse_restores_original_entries": True,
    "added_exact_hidden_operations": 8,
    "methods": ["GET", "HEAD"],
    "paths": paths,
    "only_new_known_helper": "open_webui.utils.slack_ui.register_slack_ui",
    "unknown_router_and_undocumented_route_errors_preserved": True,
    "no_api_operation_hidden_or_changed_by_overlay": True,
    "live_replacement_candidates_unchanged": True,
    "canonical_sources_unchanged": all((REPO / name).read_bytes() == before[name] for name in files),
    "deployment_manifest_sha256": sha((HERE / "deployment-manifest.json").read_bytes()),
    "current_source_references": {str(slack_path): sha(slack_path.read_bytes()), str(main_path): sha(main_path.read_bytes())},
    "tests_added": 2,
    "tests_executed": 0,
    "runtime_actions": 0,
}
assert record["deployment_manifest_sha256"] == "6eb9e44f54af431b57f2bf40368aca0a0bf28aacca212d1892a64baa9286805e"
with (OUT / "composition.json").open("x") as target:
    target.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
print(json.dumps(record, sort_keys=True))
