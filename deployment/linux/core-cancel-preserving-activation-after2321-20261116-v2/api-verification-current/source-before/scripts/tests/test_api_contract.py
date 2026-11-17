"""Regression tests for the dependency-free API source guard."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "check_api_contract.py"
SPEC = importlib.util.spec_from_file_location("check_api_contract", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.write("backend/open_webui/main.py", """from fastapi import FastAPI
from open_webui.routers import developer
app = FastAPI()
app.include_router(developer.router, prefix="/api/v1/developer")
raise RuntimeError("source must never be imported")
""")
        self.write("backend/open_webui/routers/developer.py", """from fastapi import APIRouter
router = APIRouter()
@router.get("/")
def catalog(): pass
@router.get("/openapi.json")
def schema(): pass
@router.get("/postman.json")
def postman(): pass
""")
        self.policy([])

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def append(self, path, content):
        target = self.root / path
        target.write_text(target.read_text(encoding="utf-8") + content, encoding="utf-8")

    def policy(self, entries):
        self.write("docs/api-contract-policy.json", json.dumps({"version": 1, "hidden_operations": entries}))

    def errors(self):
        return "\n".join(guard.check(self.root)[1])

    def test_source_is_not_imported(self):
        operations, errors = guard.check(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(len(operations), 3)

    def test_new_public_operation_needs_no_inventory_update(self):
        self.append("backend/open_webui/main.py", """
@app.post("/brand-new-feature")
def feature(payload: dict): pass
""")
        operations, errors = guard.check(self.root)
        self.assertEqual(errors, [])
        self.assertIn(("POST", "/brand-new-feature"), {(o.method, o.path) for o in operations})

    def test_unregistered_new_router_fails(self):
        self.write("backend/open_webui/routers/new_feature.py", """from fastapi import APIRouter
router = APIRouter()
@router.get("/")
def feature(): pass
""")
        self.assertIn("unregistered router", self.errors())

    def test_new_hidden_route_fails(self):
        self.append("backend/open_webui/main.py", """
@app.get("/hidden", include_in_schema=False)
def hidden(): pass
""")
        self.assertIn("undocumented HTTP operation GET /hidden", self.errors())

    def test_hidden_parent_is_detected(self):
        path = self.root / "backend/open_webui/main.py"
        path.write_text(path.read_text().replace('prefix="/api/v1/developer"', 'prefix="/api/v1/developer", include_in_schema=False'))
        errors = self.errors()
        self.assertIn("undocumented HTTP operation", errors)
        self.assertIn("required developer documentation endpoint", errors)

    def test_precise_exception_and_stale_exception(self):
        self.append("backend/open_webui/main.py", """
@app.get("/hidden", include_in_schema=False)
def hidden(): pass
""")
        self.policy([{"source": "backend/open_webui/main.py", "path": "/hidden", "methods": ["GET"], "reason": "HTML shell intentionally outside the JSON API contract."}])
        self.assertEqual(self.errors(), "")
        self.policy([{"source": "backend/open_webui/main.py", "path": "/hidden", "methods": ["GET", "POST"], "reason": "HTML shell intentionally outside the JSON API contract."}])
        self.assertIn("stale API visibility exception", self.errors())

    def test_nested_prefixes_aliases_and_literal_methods(self):
        self.write("backend/open_webui/routers/parent.py", """from fastapi import APIRouter as Router
from open_webui.routers.child import router as child_router
router = Router(prefix="/inside")
router.include_router(child_router, prefix="/child")
""")
        self.write("backend/open_webui/routers/child.py", """from fastapi import APIRouter
METHODS = ["GET", "POST"]
router = APIRouter(prefix="/own")
@router.api_route("/item", methods=METHODS)
def item(): pass
""")
        self.append("backend/open_webui/main.py", """
from open_webui.routers import parent as parent_api
app.include_router(parent_api.router, prefix="/outside")
""")
        operations, errors = guard.check(self.root)
        self.assertEqual(errors, [])
        self.assertEqual({o.method for o in operations if o.path == "/outside/inside/child/own/item"}, {"GET", "POST"})

    def test_dynamic_path_is_an_explicit_error(self):
        self.append("backend/open_webui/main.py", """
@app.get(make_path())
def unknown(): pass
""")
        self.assertIn("unsupported API registration", self.errors())

    def test_missing_included_module_fails(self):
        self.append("backend/open_webui/main.py", """
from open_webui.routers import vanished
app.include_router(vanished.router, prefix="/gone")
""")
        self.assertIn("missing or unresolved", self.errors())

    def test_removed_developer_registration_fails(self):
        self.write("backend/open_webui/main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        self.assertIn("required developer documentation endpoint", self.errors())

    def test_direct_api_route_keyword_arguments(self):
        self.append("backend/open_webui/main.py", """
app.add_api_route(path="/keyword", endpoint=lambda: {}, methods=["PATCH"])
""")
        operations, errors = guard.check(self.root)
        self.assertEqual(errors, [])
        self.assertIn(("PATCH", "/keyword"), {(o.method, o.path) for o in operations})

    def test_unsupported_router_alias_is_not_silently_skipped(self):
        self.append("backend/open_webui/main.py", """
unknown = app
@unknown.get("/alias")
def alias(): pass
""")
        self.assertIn("unresolved API router unknown", self.errors())

    def test_optional_ui_literal_loop_and_exception(self):
        self.write("backend/open_webui/utils/developer_ui.py", """def register_developer_ui(app, folder):
    for path in ("/developers", "/developers/"):
        app.add_api_route(path, lambda: "", methods=["GET", "HEAD"], include_in_schema=False)
""")
        self.append("backend/open_webui/main.py", """
from open_webui.utils.developer_ui import register_developer_ui
register_developer_ui(app, "frontend")
""")
        self.policy([{"source": "backend/open_webui/utils/developer_ui.py", "path": path, "methods": ["GET", "HEAD"], "reason": "Optional HTML developer shell, backed by documented JSON exports."} for path in ("/developers", "/developers/")])
        operations, errors = guard.check(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(sum(not o.visible for o in operations), 4)


if __name__ == "__main__":
    unittest.main()
