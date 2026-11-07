"""Standalone Developers UI regressions; no application or database is imported.

Run: python -m unittest discover -s backend/tests -p test_developer_ui.py -v
"""
import html
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

_SPEC = importlib.util.spec_from_file_location(
    "abit_developer_ui",
    Path(__file__).resolve().parents[1] / "open_webui/utils/developer_ui.py",
)
_UI = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_UI)
register_developer_ui = _UI.register_developer_ui
render_developer_navigation = _UI.render_developer_navigation


class DeveloperStaticFiles(StaticFiles):
    def file_response(self, full_path, stat_result, scope, status_code=200):
        injected = render_developer_navigation(
            full_path, scope, status_code, frontend_build_dir=self.directory
        )
        if injected is not None:
            return injected
        return super().file_response(full_path, stat_result, scope, status_code)

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or path.endswith(".js"):
                raise
            return await super().get_response("index.html", scope)


class DeveloperUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.index = self.root / "index.html"
        self.original = b"<!doctype html>\r\n<html><head><title>ABiT</title></head><body>Existing UI</body></html>"
        self.index.write_bytes(self.original)
        self.bundle = self.root / "developer-ui"
        self.bundle.mkdir()
        self.page = self.bundle / "index.html"
        self.page.write_bytes(b"<html><head></head><body>Developers</body></html>")
        self.navigation = self.bundle / "navigation.js"
        self.navigation.write_text("/* navigation bundle */", encoding="utf-8")

    def scope(self, **values):
        return {"type": "http", "method": "GET", "root_path": "", **values}

    def render(self, **scope_values):
        return render_developer_navigation(
            self.index, self.scope(**scope_values), frontend_build_dir=self.root
        )

    def app(self, register=True):
        app = FastAPI()
        if register:
            register_developer_ui(app, self.root)
        app.mount("/", DeveloperStaticFiles(directory=self.root, html=True))
        return app

    def test_root_index_only_changes_by_external_script(self):
        response = self.render()
        self.assertIsNotNone(response)
        script = re.search(rb'<script defer src="[^"]+"></script>', response.body)
        self.assertIsNotNone(script)
        self.assertEqual(response.body.replace(script.group(), b"", 1), self.original)
        self.assertIn(b'/developer-ui/navigation.js?v=', script.group())
        self.assertLess(response.body.index(script.group()), response.body.index(b"</head>"))

    def test_root_prefix_is_html_escaped_and_never_uses_host_or_query(self):
        prefix = '/team"&<example>'
        response = self.render(
            root_path=prefix,
            headers=[(b"host", b"attacker.invalid")],
            query_string=b"src=https://attacker.invalid/script.js",
        )
        expected = html.escape(prefix + "/developer-ui/navigation.js?v=", quote=True)
        self.assertIn(expected.encode(), response.body)
        self.assertNotIn(b"attacker.invalid", response.body)
        self.assertNotIn(b'<example>', response.body)

    def test_trailing_prefix_slash_is_normalized(self):
        self.assertIn(b'src="/team/developer-ui/', self.render(root_path="/team/").body)

    def test_invalid_prefix_is_not_injected(self):
        for prefix in ("//attacker.invalid", "https://attacker.invalid", "/a\\b", "/a?b", "/a#b", "/a\r\nb", "/../a"):
            with self.subTest(prefix=prefix):
                self.assertIsNone(self.render(root_path=prefix))

    def test_other_file_and_nested_index_are_untouched(self):
        for relative in ("other.html", "nested/index.html", "developer-ui/index.html"):
            path = self.root / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(self.original)
            self.assertIsNone(
                render_developer_navigation(path, self.scope(), frontend_build_dir=self.root)
            )

    def test_missing_bundle_preserves_frontend_and_adds_no_route(self):
        for missing in (self.page, self.navigation):
            with self.subTest(missing=missing.name):
                old = missing.read_bytes()
                missing.unlink()
                self.assertIsNone(self.render())
                app = FastAPI()
                self.assertFalse(register_developer_ui(app, self.root))
                self.assertFalse(any(route.path.startswith("/developers") for route in app.routes))
                app.mount("/", DeveloperStaticFiles(directory=self.root, html=True))
                self.assertEqual(TestClient(app).get("/developers").content, self.original)
                missing.write_bytes(old)

    def test_no_root_or_non_http_or_unsupported_method_or_status_falls_back(self):
        self.assertIsNone(render_developer_navigation(self.index, self.scope()))
        for values in ({"type": "websocket"}, {"method": "POST"}):
            self.assertIsNone(self.render(**values))
        self.assertIsNone(
            render_developer_navigation(self.index, self.scope(), 404, frontend_build_dir=self.root)
        )

    def test_missing_head_or_root_index_falls_back(self):
        self.index.write_bytes(b"<html><body>No head</body></html>")
        self.assertIsNone(self.render())
        self.index.unlink()
        self.assertIsNone(self.render())

    def test_case_insensitive_head_and_head_body_handling(self):
        self.index.write_bytes(b"<html><HEAD></HEAD ><body>Existing</body></html>")
        get_response = self.render()
        head_response = self.render(method="HEAD")
        self.assertIn(b"navigation.js", get_response.body)
        self.assertEqual(head_response.body, b"")
        self.assertEqual(head_response.headers["content-length"], str(len(get_response.body)))

    def test_private_cache_headers_have_no_file_validators(self):
        response = self.render()
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["pragma"], "no-cache")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("etag", response.headers)
        self.assertNotIn("last-modified", response.headers)

    def test_navigation_change_updates_asset_version(self):
        first = self.render().body
        self.navigation.write_bytes(b"/* changed navigation bundle */")
        self.assertNotEqual(first, self.render().body)

    def test_registration_serves_both_paths_with_head_and_no_openapi_operation(self):
        app = self.app()
        client = TestClient(app)
        for path in ("/developers", "/developers/"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, self.page.read_bytes())
                self.assertEqual(response.headers["cache-control"], "private, no-store")
                self.assertNotIn("etag", response.headers)
                head = client.head(path)
                self.assertEqual(head.content, b"")
                self.assertEqual(head.headers["content-length"], response.headers["content-length"])
        self.assertNotIn("/developers", app.openapi()["paths"])
        self.assertIsNotNone(render_developer_navigation(self.index, self.scope(app=app)))

    def test_bundle_removal_after_registration_returns_404(self):
        client = TestClient(self.app())
        self.page.unlink()
        self.assertEqual(client.get("/developers").status_code, 404)

    def test_spa_fallback_injects_without_revalidation_and_keeps_static_caching(self):
        asset = self.root / "asset.js"
        asset.write_bytes(b"/* existing frontend asset */")
        client = TestClient(self.app())
        for path in ("/", "/existing-chat"):
            response = client.get(
                path,
                headers={"If-None-Match": "*", "If-Modified-Since": "Fri, 01 Jan 2100 00:00:00 GMT"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"navigation.js", response.content)
            self.assertNotIn("etag", response.headers)
        original_asset = client.get("/asset.js")
        self.assertEqual(original_asset.content, asset.read_bytes())
        self.assertIn("etag", original_asset.headers)
        self.assertEqual(
            client.get("/asset.js", headers={"If-None-Match": original_asset.headers["etag"]}).status_code,
            304,
        )
        self.assertEqual(client.get("/missing.js").status_code, 404)

    def test_root_path_works_through_static_mount(self):
        client = TestClient(self.app(), root_path="/inhouse")
        response = client.get("/inhouse/existing-chat")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'src="/inhouse/developer-ui/navigation.js?', response.content)


if __name__ == "__main__":
    unittest.main()
