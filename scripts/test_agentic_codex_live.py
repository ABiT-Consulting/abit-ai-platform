"""Manual authenticated CLI/MCP smoke test; synthetic ERP scope only."""
import asyncio,json,sys
sys.path.insert(0,'backend/tests')
from test_agentic_codex import Execution
async def main():
    f=Execution('test_actual_stdio_mcp_transport_and_capability_check')
    f.setUp()
    try:
        await f.admitted()
        f.engine.begin(f.row,f.path)
        tools=f.codex.LocalTools(f.row,f.packet(),None,f.bridge,f.config,f.gate,f.scope(f.row),f.path)
        tools.registry=f.registry
        outcome=await f.codex.run_cli(f.row,
            'Integration smoke test in an isolated synthetic ABIT task. Call the abit agentic_action MCP tool once with call_id codex-smoke-read, operation read.fixture, arguments {}. Then return CLI_MCP_OK. Do not call context.read or any other operation.',
            {'agentic_codex':{'timeout_seconds':90}},tools)
        print(json.dumps({'exit_code':outcome['exit_code'],'reason':outcome.get('reason'),
                          'mcp_handler_calls':f.calls,'reply_ok':'CLI_MCP_OK' in outcome['reply'],
                          'provider_events':outcome.get('events',[]), 'reply_preview':outcome['reply'][:1500], 'diagnostics':outcome.get('diagnostics','')[-5000:]}))
        if outcome['exit_code']!=0 or f.calls!=1 or 'CLI_MCP_OK' not in outcome['reply']:
            raise SystemExit(1)
    finally:
        f.doCleanups()
asyncio.run(main())
