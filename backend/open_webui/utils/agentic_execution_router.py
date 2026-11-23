"""Select an executor only for a new governed run. Existing receipts win policy."""
from open_webui.utils import agentic_execution_engine as engine
from open_webui.utils.agentic_executor import DotAdapter


def select_adapter(app, row, config, project_gate, db_path=None):
    if not row or row['target_role'] != 'developer':
        return DotAdapter(app)
    original = engine.get(row['invocation_id'], db_path, private=True)
    strategy = engine.policy(row['erp_project'], db_path)['strategy']
    if original or strategy == 'codex_first':
        from open_webui.utils.agentic_codex import CodexAdapter
        return CodexAdapter(app, project_gate, config, db_path)
    return DotAdapter(app)
