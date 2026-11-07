#!/usr/bin/env python3
"""Check API registration/visibility without importing the application.

This source-only gate inventories statically declared FastAPI HTTP operations.
Conditional registrations are checked as a union; the integration tests verify
the running application's OpenAPI/Postman exports. UI business intent cannot be
inferred here and remains part of the repository's API maintenance policy.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path
import sys

HTTP_METHODS = frozenset(("get", "post", "put", "patch", "delete", "options", "head", "trace"))
MAIN = "open_webui.main.app"
UI_MODULE = "open_webui.utils.developer_ui"


@dataclass(frozen=True)
class Operation:
    source: str
    line: int
    method: str
    path: str
    visible: bool


class Source:
    def __init__(self, path: Path, root: Path):
        self.path = path.relative_to(root).as_posix()
        self.module = ".".join(path.relative_to(root / "backend").with_suffix("").parts)
        if self.module.endswith(".__init__"):
            self.module = self.module[:-9]
        self.tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=self.path)
        self.parents = {child: parent for parent in ast.walk(self.tree) for child in ast.iter_child_nodes(parent)}
        self.imports = {}
        self.constants = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    self.imports[name.asname or name.name.split(".")[0]] = name.name if name.asname else name.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    package = self.module.split(".")[:-node.level]
                    module = ".".join(package + ([module] if module else []))
                for name in node.names:
                    self.imports[name.asname or name.name] = module + "." + name.name
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and node.value is not None:
                        try:
                            self.constants[target.id] = ast.literal_eval(node.value)
                        except (ValueError, TypeError):
                            pass

    def ref(self, node):
        if isinstance(node, ast.Name):
            return self.imports.get(node.id, self.module + "." + node.id)
        if isinstance(node, ast.Attribute):
            base = self.ref(node.value)
            return base + "." + node.attr if base else None
        return None

    def value(self, node):
        if isinstance(node, ast.Name) and node.id in self.constants:
            return self.constants[node.id]
        return ast.literal_eval(node)

    def paths(self, node):
        # The optional HTML UI registers two literal paths in a simple for loop.
        if isinstance(node, ast.Name):
            parent = node
            while parent in self.parents:
                parent = self.parents[parent]
                if isinstance(parent, ast.For) and isinstance(parent.target, ast.Name) and parent.target.id == node.id:
                    values = self.value(parent.iter)
                    if isinstance(values, (list, tuple)) and all(isinstance(x, str) for x in values):
                        return values
        value = self.value(node)
        if not isinstance(value, str):
            raise ValueError("path must be a literal string")
        return [value]


def keyword(call, name, default, source):
    item = next((k for k in call.keywords if k.arg == name), None)
    return default if item is None else source.value(item.value)


def check(root: Path, policy_path: Path | None = None):
    """Return (operations, errors). Never import backend modules or read data."""
    errors = []
    sources = []
    backend = root / "backend" / "open_webui"
    if not backend.is_dir():
        return [], ["backend/open_webui is missing"]
    for path in sorted(backend.rglob("*.py")):
        try:
            sources.append(Source(path, root))
        except (SyntaxError, OSError, UnicodeError) as error:
            errors.append(f"{path.relative_to(root)}: cannot parse source: {error}")
    objects = {}
    declarations = {}
    includes = {}
    helpers = []
    by_module = {source.module: source for source in sources}

    def failure(source, node, message):
        errors.append(f"{source.path}:{node.lineno}: {message}")

    for source in sources:
        for node in ast.walk(source.tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, ast.Call):
                continue
            kind = source.ref(node.value.func)
            if kind not in ("fastapi.APIRouter", "fastapi.FastAPI", "fastapi.routing.APIRouter", "fastapi.applications.FastAPI"):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    failure(source, node, "unsupported router assignment; use a named router")
                    continue
                key = source.module + "." + target.id
                try:
                    prefix = keyword(node.value, "prefix", "", source)
                    visible = keyword(node.value, "include_in_schema", True, source)
                    if not isinstance(prefix, str) or type(visible) is not bool:
                        raise ValueError("prefix/visibility must be literal")
                    objects[key] = (prefix, visible)
                except (ValueError, TypeError) as error:
                    failure(source, node, f"unsupported router configuration: {error}")
    if MAIN not in objects:
        errors.append("main.py must declare the FastAPI app")
    main = by_module.get("open_webui.main")
    if main:
        for node in ast.walk(main.tree):
            if isinstance(node, ast.Call) and main.ref(node.func) == UI_MODULE + ".register_developer_ui":
                if not node.args or main.ref(node.args[0]) != MAIN:
                    failure(main, node, "developer UI registration must target the main app")
                else:
                    helpers.append(UI_MODULE + ".app")
                    objects[UI_MODULE + ".app"] = ("", True)

    for source in sources:
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            action = node.func.attr
            receiver = source.ref(node.func.value)
            is_registration = action in HTTP_METHODS or action in ("api_route", "add_api_route", "include_router", "route", "add_route")
            if not is_registration:
                continue
            if receiver not in objects:
                # Only flag apparent FastAPI declarations, not requests/session.get().
                owner_name = ast.unparse(node.func.value)
                parent = source.parents.get(node)
                is_decorator = isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and node in parent.decorator_list
                if action in ("include_router", "add_api_route") or is_decorator:
                    failure(source, node, f"unresolved API router {owner_name}; use a statically named/imported router")
                continue
            try:
                if any(k.arg is None for k in node.keywords):
                    raise ValueError("**kwargs registration is not statically supported")
                visible = keyword(node, "include_in_schema", True, source)
                if type(visible) is not bool:
                    raise ValueError("include_in_schema must be a literal boolean")
                if action == "include_router":
                    target_node = node.args[0] if node.args else next(k.value for k in node.keywords if k.arg == "router")
                    target = source.ref(target_node)
                    if target not in objects:
                        raise ValueError(f"included router {ast.unparse(target_node)} is missing or unresolved")
                    prefix = keyword(node, "prefix", "", source)
                    if not isinstance(prefix, str):
                        raise ValueError("include prefix must be a literal string")
                    includes.setdefault(receiver, []).append((target, prefix, visible))
                    continue
                if action in ("route", "add_route"):
                    raise ValueError("plain Starlette HTTP registration is not exported by OpenAPI; use api_route or add_api_route")
                path_node = node.args[0] if node.args else next(k.value for k in node.keywords if k.arg == "path")
                methods = [action.upper()] if action in HTTP_METHODS else keyword(node, "methods", ["GET"], source)
                if not isinstance(methods, (list, tuple, set)) or not methods or any(not isinstance(m, str) or m.lower() not in HTTP_METHODS for m in methods):
                    raise ValueError("methods must be a nonempty literal HTTP method list")
                for path in source.paths(path_node):
                    for method in methods:
                        declarations.setdefault(receiver, []).append(Operation(source.path, node.lineno, method.upper(), path, visible))
            except (ValueError, TypeError, StopIteration) as error:
                failure(source, node, f"unsupported API registration: {error}")

    operations = []
    reached = set()

    def visit(key, prefix="", visible=True, stack=()):
        if key in stack:
            errors.append(f"router include cycle: {' -> '.join(stack + (key,))}")
            return
        reached.add(key)
        own_prefix, own_visible = objects[key]
        prefix += own_prefix
        visible = visible and own_visible
        for operation in declarations.get(key, []):
            path = prefix + operation.path
            if not path.startswith("/"):
                errors.append(f"{operation.source}:{operation.line}: API path must begin with /: {path!r}")
            operations.append(Operation(operation.source, operation.line, operation.method, path, visible and operation.visible))
        for child, child_prefix, child_visible in includes.get(key, []):
            visit(child, prefix + child_prefix, visible and child_visible, stack + (key,))

    for key in [MAIN] + helpers:
        if key in objects:
            visit(key)
    for key in sorted(set(declarations) - reached):
        errors.append(f"unregistered router with HTTP operations: {key}")
    required = {"/api/v1/developer/", "/api/v1/developer/openapi.json", "/api/v1/developer/postman.json"}
    actual = {o.path for o in operations if o.source == "backend/open_webui/routers/developer.py" and o.method == "GET" and o.visible}
    for path in sorted(required - actual):
        errors.append(f"required developer documentation endpoint is missing or hidden: GET {path}")

    policy_path = policy_path or root / "docs" / "api-contract-policy.json"
    approved = {}
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8-sig"))
        if policy.get("version") != 1 or not isinstance(policy.get("hidden_operations"), list):
            raise ValueError("expected version 1 and hidden_operations list")
        for entry in policy["hidden_operations"]:
            if set(entry) != {"source", "path", "methods", "reason"}:
                raise ValueError("each exception requires source, path, methods, reason")
            if not isinstance(entry["reason"], str) or len(entry["reason"].strip()) < 20:
                raise ValueError("hidden operation exceptions need a meaningful reason")
            if not isinstance(entry["source"], str) or not isinstance(entry["path"], str) or not entry["path"].startswith("/") or "*" in entry["path"]:
                raise ValueError("exceptions require exact source and path (no wildcards)")
            if not isinstance(entry["methods"], list) or not entry["methods"] or any(m not in {x.upper() for x in HTTP_METHODS} for m in entry["methods"]):
                raise ValueError("exceptions require explicit HTTP methods")
            for method in entry["methods"]:
                key = (entry["source"], method, entry["path"])
                if key in approved:
                    raise ValueError(f"duplicate hidden exception: {key}")
                approved[key] = entry["reason"]
    except (OSError, ValueError, TypeError, KeyError) as error:
        errors.append(f"{policy_path}: invalid API visibility policy: {error}")
    hidden = {(o.source, o.method, o.path) for o in operations if not o.visible}
    for source, method, path in sorted(hidden - approved.keys()):
        errors.append(f"{source}: undocumented HTTP operation {method} {path}; expose it or add a precise justified visibility exception")
    for source, method, path in sorted(approved.keys() - hidden):
        errors.append(f"stale API visibility exception: {source} {method} {path}")
    return operations, errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="repository source root (default: this script's repository)")
    args = parser.parse_args(argv)
    operations, errors = check(args.root.resolve())
    if errors:
        print("API contract source guard FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    hidden = sum(not operation.visible for operation in operations)
    print(f"API contract source guard passed: {len(operations)} declared HTTP operations; {hidden} justified hidden operations. No application imports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
