"""API catalog security, schema fidelity and credential-free export regressions.

Run with: python -m unittest discover -s backend/tests -p test_developer_api.py -v
No application environment, database, live service or credentials are loaded.
"""
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_webui.utils.developer_api import (
    api_key_access,
    build_postman,
    build_schema,
    catalog_counts,
    realtime_catalog,
)


class FakeConfig:
    values = {}

    @classmethod
    async def get_many(cls, *keys):
        return {key: cls.values.get(key) for key in keys}


async def has_permission(user_id, permission, permissions):
    return bool((permissions or {}).get("features", {}).get("api_keys"))


async def get_current_user(authorization: str | None = Header(None)):
    accounts = {
        "Bearer user-session": SimpleNamespace(id="user-one", role="user"),
        "Bearer admin-session": SimpleNamespace(id="admin-one", role="admin"),
        "Bearer pending-session": SimpleNamespace(id="pending-one", role="pending"),
    }
    user = accounts.get(authorization)
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user


def get_verified_user(user=Depends(get_current_user)):
    if user.role not in {"user", "admin"}:
        raise HTTPException(401, "Access prohibited")
    return user


def get_admin_user(user=Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(401, "Access prohibited")
    return user


def nested_admin(user=Depends(get_admin_user)):
    return user


async def get_scim_auth():
    return True


class ChatInput(BaseModel):
    title: str = Field(description="A new chat title")
    folder_id: str | None = None


class ChatOutput(BaseModel):
    id: str
    title: str


class AdminOnlyOutput(BaseModel):
    internal_setting: str


def policy(enabled=True, permitted=True, restricted=False, paths=None):
    return {
        "api_keys_enabled": enabled,
        "can_manage_api_key": permitted,
        "endpoint_restrictions": restricted,
        "allowed_endpoints": paths or [],
    }


def application():
    app = FastAPI()

    @app.get("/api/v1/chats/{chat_id}", response_model=ChatOutput, tags=["chats"])
    async def read_chat(chat_id: str, user=Depends(get_verified_user)):
        return {"id": chat_id, "title": "Example"}

    @app.post("/api/v1/chats/", response_model=ChatOutput, tags=["chats"])
    async def create_chat(body: ChatInput, user=Depends(get_verified_user)):
        return {"id": "new-chat", "title": body.title}

    @app.get("/api/v1/configs/private", response_model=AdminOnlyOutput, tags=["configs"])
    async def admin_config(user=Depends(nested_admin)):
        return {"internal_setting": "example"}

    @app.post("/api/chat/completions")
    async def completions(body: dict, user=Depends(get_verified_user)):
        return body

    @app.post("/api/v1/files/", tags=["files"])
    async def upload(file: UploadFile = File(...), user=Depends(get_verified_user)):
        return {"name": file.filename}

    @app.get("/health")
    async def health():
        return True

    @app.get("/api/v1/hidden", include_in_schema=False)
    async def hidden():
        return True

    @app.get("/manifest.json")
    async def manifest():
        return {}

    @app.get("/")
    async def spa():
        return {}

    @app.get("/api/v1/chatgpt-relay/status")
    async def relay(user=Depends(get_verified_user)):
        return {}

    @app.post("/api/v1/knowledge/reindex")
    async def reindex(user=Depends(get_verified_user)):
        return {}

    @app.get("/api/v1/scim/v2/Users")
    async def scim(user=Depends(get_scim_auth)):
        return {}

    @app.post("/api/v1/telegram/mcp")
    async def telegram_mcp():
        return {}

    @app.post("/api/v1/telegram/webhook")
    async def telegram_webhook():
        return {}

    @app.get("/oauth/google/callback")
    async def oauth():
        return {}

    @app.websocket("/api/v1/terminals/{server_id}/api/terminals/{session_id}")
    async def websocket():
        pass

    async def proxy(path: str, user=Depends(get_verified_user)):
        return {}
    app.add_api_route("/openai/{path:path}", proxy, methods=["GET", "POST", "PUT"])
    app.mount("/ws", FastAPI())
    app.mount("/static", FastAPI())
    return app


def load_documentation_router():
    """Import only the router, replacing services which would initialize the database."""
    replacements = {}
    for name, members in {
        "open_webui.env": {"VERSION": "test-version"},
        "open_webui.models.config": {"Config": FakeConfig},
        "open_webui.utils.access_control": {"has_permission": has_permission},
        "open_webui.utils.auth": {"get_verified_user": get_verified_user},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(members)
        replacements[name] = module
    module_path = Path(__file__).resolve().parents[1] / "open_webui" / "routers" / "developer.py"
    spec = importlib.util.spec_from_file_location("_abit_developer_test_router", module_path)
    module = importlib.util.module_from_spec(spec)
    replacements[spec.name] = module
    with patch.dict(sys.modules, replacements):
        spec.loader.exec_module(module)
    return module


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.app = application()

    def schema(self, role="user", authentication=None):
        return build_schema(self.app, role, "test-version", authentication or policy())

    def test_only_registered_documented_api_routes_are_exported(self):
        schema = self.schema()
        self.assertIn("/api/v1/chats/{chat_id}", schema["paths"])
        self.assertIn("/health", schema["paths"])
        for excluded in ("/", "/manifest.json", "/api/v1/hidden", "/static", "/api/v1/not-implemented"):
            self.assertNotIn(excluded, schema["paths"])

    def test_recursive_and_inline_admin_checks_are_filtered(self):
        regular = self.schema()
        administrator = self.schema("admin")
        for path in ("/api/v1/configs/private", "/api/v1/chatgpt-relay/status", "/api/v1/knowledge/reindex"):
            self.assertNotIn(path, regular["paths"])
            self.assertIn(path, administrator["paths"])
            method = "post" if path.endswith("reindex") else "get"
            self.assertEqual(administrator["paths"][path][method]["x-abit-access"], "admin")
        self.assertNotIn("AdminOnlyOutput", regular["components"]["schemas"])
        self.assertIn("AdminOnlyOutput", administrator["components"]["schemas"])

    def test_request_response_references_and_parameters_survive(self):
        schema = self.schema()
        create = schema["paths"]["/api/v1/chats/"]["post"]
        self.assertEqual(create["requestBody"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/ChatInput")
        self.assertEqual(create["responses"]["200"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/ChatOutput")
        fields = schema["components"]["schemas"]["ChatInput"]["properties"]
        self.assertEqual(fields["title"]["description"], "A new chat title")
        self.assertTrue(any(p["name"] == "chat_id" and p["required"] for p in schema["paths"]["/api/v1/chats/{chat_id}"]["get"]["parameters"]))

    def test_cache_does_not_mix_roles_policy_or_mutations(self):
        admin = self.schema("admin")
        admin["paths"].clear()
        user = self.schema(authentication=policy(enabled=False))
        self.assertTrue(user["paths"])
        self.assertNotIn("/api/v1/configs/private", user["paths"])
        self.assertEqual(user["paths"]["/api/v1/chats/"]["post"]["x-abit-api-key-access"], "disabled")
        self.assertEqual(self.schema()["paths"]["/api/v1/chats/"]["post"]["x-abit-api-key-access"], "allowed")
        self.assertIn("/api/v1/configs/private", self.schema("admin")["paths"])

    def test_cache_refreshes_when_routes_are_registered(self):
        self.schema()
        async def added(user=Depends(get_verified_user)):
            return {}
        self.app.add_api_route("/api/v1/new-feature", added, methods=["GET"])
        self.assertIn("/api/v1/new-feature", self.schema()["paths"])

    def test_multimethod_routes_have_unique_ids_and_correct_counts(self):
        schema = self.schema()
        ids = []
        for operations in schema["paths"].values():
            ids.extend(operation["operationId"] for method, operation in operations.items() if method in {"get", "post", "put", "patch", "delete", "options", "head", "trace"})
        self.assertEqual(len(ids), len(set(ids)))
        counts, groups = catalog_counts(schema)
        self.assertEqual(counts["operations"], len(ids))
        self.assertEqual(sum(group["count"] for group in groups), len(ids))
        self.assertEqual(counts["paths"], len(schema["paths"]))
        self.assertEqual(set(schema["paths"]["/openai/{path}"]), {"get", "post", "put"})

    def test_chat_completion_example_and_streaming_are_documented(self):
        operation = self.schema()["paths"]["/api/chat/completions"]["post"]
        body = operation["requestBody"]["content"]["application/json"]["example"]
        self.assertEqual(body["model"], "YOUR_MODEL_ID")
        self.assertEqual(body["messages"], [{"role": "user", "content": "Hello"}])
        self.assertFalse(body["stream"])
        self.assertIn("text/event-stream", operation["responses"]["200"]["content"])

    def test_special_auth_is_not_advertised_as_personal_api_key(self):
        schema = self.schema("admin")
        expected = {
            "/api/v1/scim/v2/Users": ("get", "SCIMBearer"),
            "/api/v1/telegram/mcp": ("post", "TelegramOAuthBearer"),
            "/api/v1/telegram/webhook": ("post", "TelegramWebhookSecret"),
        }
        for path, (method, security) in expected.items():
            operation = schema["paths"][path][method]
            self.assertEqual(operation["x-abit-access"], "custom")
            self.assertEqual(operation["x-abit-api-key-access"], "custom")
            self.assertEqual(operation["security"], [{security: []}])
        self.assertNotIn("/api/v1/scim/v2/Users", self.schema()["paths"])
        self.assertEqual(schema["paths"]["/oauth/google/callback"]["get"]["x-abit-api-key-access"], "custom")
        self.assertEqual(schema["paths"]["/health"]["get"]["x-abit-api-key-access"], "not-required")

    def test_realtime_is_reported_separately_from_http(self):
        schema = self.schema()
        realtime = realtime_catalog(self.app)
        self.assertEqual(realtime["socket_io_mounts"], ["/ws"])
        self.assertEqual(len(realtime["websocket_paths"]), 1)
        self.assertNotIn(realtime["websocket_paths"][0], schema["paths"])
        self.assertIn("JWT", realtime["note"])

    def test_prefix_restrictions_match_boundaries_and_concrete_ids(self):
        restricted = policy(restricted=True, paths=["/api/v1/chats"])
        self.assertEqual(api_key_access("/api/v1/chats/{id}", "authenticated", restricted), "allowed")
        self.assertEqual(api_key_access("/api/v1/chats-archive", "authenticated", restricted), "restricted")
        scoped = policy(restricted=True, paths=["/api/v1/chats/specific-id"])
        self.assertEqual(api_key_access("/api/v1/chats/{id}", "authenticated", scoped), "conditional")
        self.assertEqual(api_key_access("/api/v1/chats", "authenticated", scoped), "restricted")
        self.assertEqual(api_key_access("/api/v1/files/{id}", "authenticated", scoped), "restricted")
        self.assertEqual(api_key_access("/api/v1/chats/{id}", "authenticated", policy(restricted=True)), "restricted")
        self.assertEqual(api_key_access("/health", "public", policy(enabled=False)), "not-required")

    def test_postman_export_has_all_operations_and_no_actual_credentials(self):
        schema = self.schema("admin")
        collection = build_postman(schema)
        requests = [item["request"] for group in collection["item"] for item in group["item"]]
        self.assertEqual(len(requests), catalog_counts(schema)[0]["operations"])
        self.assertEqual([item["value"] for item in collection["variable"]], ["", ""])
        self.assertEqual(collection["auth"]["bearer"][0]["value"], "{{api_key}}")
        self.assertNotIn("user-session", json.dumps(collection))
        self.assertNotIn("admin-session", json.dumps(collection))
        normal = next(item for item in requests if item["method"] == "GET" and "/chats/:chat_id" in item["url"]["raw"])
        self.assertEqual(normal["url"]["variable"][0]["key"], "chat_id")
        for request in requests:
            if "/scim/" in request["url"]["raw"] or "/telegram/mcp" in request["url"]["raw"] or "/oauth/" in request["url"]["raw"]:
                self.assertEqual(request["auth"], {"type": "noauth"})

    def test_postman_upload_uses_file_field_without_forced_multipart_boundary(self):
        collection = build_postman(self.schema())
        request = next(item["request"] for group in collection["item"] for item in group["item"] if item["request"]["url"]["raw"] == "{{base_url}}/api/v1/files/")
        self.assertEqual(request["body"]["mode"], "formdata")
        self.assertEqual(request["body"]["formdata"][0]["type"], "file")
        self.assertFalse(any(header["key"] == "Content-Type" for header in request["header"]))

    def test_custom_response_wrapper_without_status_signature_is_documented_without_mutation(self):
        class WrappedResponse(JSONResponse):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
        async def wrapped(user=Depends(get_verified_user)):
            return {"ok": True}
        self.app.add_api_route("/api/v1/wrapped", wrapped, methods=["GET"], response_class=WrappedResponse)
        original = self.app.routes[-1]
        self.assertIsNone(original.status_code)
        schema = self.schema()
        self.assertIn("200", schema["paths"]["/api/v1/wrapped"]["get"]["responses"])
        self.assertIsNone(original.status_code)
        self.assertIs(original.response_class, WrappedResponse)

    def test_postman_required_query_values_are_url_encoded(self):
        schema = self.schema()
        operation = schema["paths"]["/api/v1/chats/{chat_id}"]["get"]
        operation.setdefault("parameters", []).append({
            "name": "search", "in": "query", "required": True,
            "schema": {"type": "string", "default": "a&b c"},
        })
        collection = build_postman(schema)
        request = next(item["request"] for group in collection["item"] for item in group["item"]
                       if item["request"]["method"] == "GET" and "/chats/:chat_id" in item["request"]["url"]["raw"])
        self.assertTrue(request["url"]["raw"].endswith("?search=a%26b%20c"))
        self.assertEqual(request["url"]["query"][0]["value"], "a&b c")

    def test_nested_media_examples_never_export_credential_values(self):
        async def example_endpoint(user=Depends(get_verified_user)):
            return {}
        self.app.add_api_route(
            "/api/v1/example-body", example_endpoint, methods=["POST"],
            openapi_extra={"requestBody": {"content": {"application/json": {
                "schema": {"type": "object"},
                "example": {"settings": {"api_key": "fake-media-secret", "name": "Example"},
                            "password": "fake-media-password"},
                "examples": {"nested": {"value": {"token": "fake-example-token"}}},
            }}}},
        )
        schema = self.schema()
        output = json.dumps(schema) + json.dumps(build_postman(schema))
        for secret in ("fake-media-secret", "fake-media-password", "fake-example-token"):
            self.assertNotIn(secret, output)
        self.assertIn("Example", output)
        self.assertIn("YOUR_API_KEY", output)

    def test_token_counts_are_not_mistaken_for_credentials(self):
        class GenerationInput(BaseModel):
            max_tokens: int = 128
            token_count: int = 12
            access_token: str = "fake-access-secret"
        async def input_endpoint(body: GenerationInput, user=Depends(get_verified_user)):
            return {}
        self.app.add_api_route("/api/v1/example-generation", input_endpoint, methods=["POST"])
        schema = self.schema()
        collection = build_postman(schema)
        request = next(item["request"] for group in collection["item"] for item in group["item"]
                       if item["request"]["url"]["raw"] == "{{base_url}}/api/v1/example-generation")
        sample = json.loads(request["body"]["raw"])
        self.assertEqual(sample["max_tokens"], 128)
        self.assertEqual(sample["token_count"], 12)
        self.assertEqual(sample["access_token"], "YOUR_ACCESS_TOKEN")
        self.assertNotIn("fake-access-secret", json.dumps(schema))

    def test_sensitive_static_defaults_are_not_exported(self):
        class SecretInput(BaseModel):
            api_key: str = "fake-test-secret-should-not-export"
            password: str = "fake-password-should-not-export"
            title: str = "Visible default"
        async def input_endpoint(body: SecretInput, user=Depends(get_verified_user)):
            return {}
        self.app.add_api_route("/api/v1/example-input", input_endpoint, methods=["POST"])
        schema = self.schema()
        output = json.dumps(schema) + json.dumps(build_postman(schema))
        self.assertNotIn("fake-test-secret-should-not-export", output)
        self.assertNotIn("fake-password-should-not-export", output)
        self.assertIn("Visible default", output)
        self.assertIn("YOUR_API_KEY", output)


class RouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_documentation_router()

    def setUp(self):
        FakeConfig.values = {
            "auth.enable_api_keys": True,
            "auth.api_key.endpoint_restrictions": False,
            "auth.api_key.allowed_endpoints": "",
            "user.permissions": {"features": {"api_keys": True}},
        }
        self.app = application()
        self.app.include_router(self.module.router, prefix="/api/v1/developer", tags=["developer"])
        self.client = TestClient(self.app)
        self.headers = {"Authorization": "Bearer user-session"}

    def tearDown(self):
        self.client.close()

    def test_all_documentation_endpoints_require_verified_authentication(self):
        for suffix in ("/", "/openapi.json", "/postman.json"):
            path = "/api/v1/developer" + suffix
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers={"Authorization": "Bearer pending-session"}).status_code, 401)
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 200)

    def test_metadata_and_exports_have_consistent_counts_and_private_headers(self):
        catalog = self.client.get("/api/v1/developer/", headers=self.headers)
        schema = self.client.get("/api/v1/developer/openapi.json", headers=self.headers)
        postman = self.client.get("/api/v1/developer/postman.json", headers=self.headers)
        for response in (catalog, schema, postman):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            self.assertEqual(response.headers["vary"], "Authorization, Cookie")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(catalog.json()["counts"], catalog_counts(schema.json())[0])
        self.assertEqual(catalog.json()["authentication"]["api_key_path"], "/api/v1/auths/api_key")
        self.assertEqual(catalog.json()["version"], "test-version")
        self.assertIn("attachment", schema.headers["content-disposition"])
        self.assertIn("attachment", postman.headers["content-disposition"])

    def test_policy_is_reported_without_enabling_keys_or_permissions(self):
        FakeConfig.values["auth.enable_api_keys"] = False
        result = self.client.get("/api/v1/developer/", headers=self.headers).json()
        self.assertFalse(result["authentication"]["api_keys_enabled"])
        self.assertFalse(result["authentication"]["can_manage_api_key"])
        self.assertFalse(FakeConfig.values["auth.enable_api_keys"])
        FakeConfig.values["auth.enable_api_keys"] = True
        FakeConfig.values["user.permissions"] = {"features": {"api_keys": False}}
        result = self.client.get("/api/v1/developer/", headers=self.headers).json()
        self.assertFalse(result["authentication"]["can_manage_api_key"])
        result = self.client.get("/api/v1/developer/", headers={"Authorization": "Bearer admin-session"}).json()
        self.assertTrue(result["authentication"]["can_manage_api_key"])

    def test_request_host_never_becomes_an_export_credential_target(self):
        headers = {**self.headers, "Host": "untrusted.example"}
        schema = self.client.get("/api/v1/developer/openapi.json", headers=headers).json()
        metadata = self.client.get("/api/v1/developer/", headers=headers).json()
        collection = self.client.get("/api/v1/developer/postman.json", headers=headers).json()
        self.assertEqual(schema["servers"][0]["url"], "/")
        self.assertEqual(metadata["base_url"], "/")
        self.assertNotIn("untrusted.example", json.dumps(collection))
        self.assertEqual(collection["variable"][0]["value"], "")


if __name__ == "__main__":
    unittest.main()
