"""Pin the captured current source in an isolated API child; no production authorization."""
from pathlib import Path
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import sys
import urllib.parse

HERE = Path(__file__).resolve().parent
RUNTIME = Path("/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui")
LOADED = {}
INPUTS = {}

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

class Loader(importlib.machinery.SourceFileLoader):
    def exec_module(self, module):
        path = Path(self.path)
        expected = INPUTS[module.__name__]
        if sha(path.read_bytes()) != expected["sha256"]:
            raise RuntimeError("Current overlay source changed")
        super().exec_module(module)
        if module.__name__ == "open_webui.utils.agentic_gateway_contracts":
            module.SCHEMAS = HERE / "snapshot/open_webui/utils/agentic_contracts/schemas"
        LOADED[module.__name__] = expected

class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        item = INPUTS.get(fullname)
        if item is None:
            return None
        file = HERE / item["path"]
        locations = [str(file.parent)] if file.name == "__init__.py" else None
        return importlib.util.spec_from_file_location(
            fullname, file, loader=Loader(fullname, str(file)), submodule_search_locations=locations)

def install(directory):
    directory = Path(directory).resolve()
    if (os.environ.get("ABIT_API_MODULE_TEST_DIR") != str(directory)
            or not directory.name.startswith("api-module-")
            or directory.parent.name != ".git"):
        raise RuntimeError("Exact isolated API directory required")
    cohort = json.loads((HERE / "api-complete-source-manifest.json").read_text())
    composition = json.loads((HERE / "composition-proof.json").read_text())
    replacements = {row["path"]: row["after_sha256"] for row in composition["replacements"]}
    for source, digest in cohort["all_source_pins"].items():
        original = Path(source)
        if not original.is_relative_to(RUNTIME) or original.suffix != ".py":
            continue
        relative = original.relative_to(RUNTIME)
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        fullname = "open_webui" + ("." + ".".join(parts) if parts else "")
        selected = ("candidate/" if str(relative) in replacements else "snapshot/") + "open_webui/" + str(relative)
        actual = replacements.get(str(relative), digest)
        file = HERE / selected
        if file.is_symlink() or sha(file.read_bytes()) != actual:
            raise RuntimeError("Pinned isolated source differs")
        INPUTS[fullname] = {"path": selected, "sha256": actual}
    if len(replacements) != 5 or "open_webui.utils.agentic_project_queue" not in INPUTS:
        raise RuntimeError("Complete current overlay and queue required")
    if "open_webui.main" not in INPUTS:
        raise RuntimeError("Captured current Main source required")

    def no_live_effects(event, values):
        if event in {"socket.connect", "subprocess.Popen"}:
            raise RuntimeError("API fixture cannot open network connections or launch processes")
        if event == "open" and values and isinstance(values[0], (str, bytes, os.PathLike)):
            value = os.fsdecode(values[0])
            resolved = Path(value).absolute()
            live = Path("/srv/abit-dev-linux/abit-ai")
            if resolved.is_relative_to(live) and not resolved.is_relative_to(live / "venv"):
                raise RuntimeError("API fixture cannot read live application files or data")
        if event == "sqlite3.connect" and values:
            value = os.fsdecode(values[0])
            if value in {":memory:", ""} or value.startswith("file::memory:"):
                return
            if value.startswith("file:"):
                value = urllib.parse.unquote(urllib.parse.urlparse(value).path)
            if not Path(value).absolute().is_relative_to(directory):
                raise RuntimeError("API fixture SQLite must remain isolated")
    sys.addaudithook(no_live_effects)
    sys.meta_path.insert(0, Finder())

def summary():
    return {
        "captured_current_modules": len(INPUTS),
        "imported_captured_modules": dict(sorted(LOADED.items())),
        "full_current_main_source_imported": "open_webui.main" in LOADED,
        "production_database_or_config_access": False,
        "network_connections_or_child_processes": False,
        "application_lifespan_started": False,
    }
