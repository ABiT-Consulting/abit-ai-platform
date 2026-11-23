"""Isolated contract check for manually resolved Codex-first router conflicts.
Imports no running application, provider, ERP bridge, browser, gate or live DB.
"""
import ast
import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROUTER = Path(__file__).resolve().parents[1] / 'open_webui/routers/agentic_governance.py'

class RouterAcceptance(unittest.TestCase):
    def test_adapter_selection_preserves_native_it_and_reconciliation_wakeup(self):
        src = ast.parse(ROUTER.read_text())
        deliver = next(n for n in src.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_deliver')
        counts = {'wakeups':0,'reconciled':0,'advances':0}
        modules={}
        def module(name, **attrs):
            o=types.ModuleType(name);o.__dict__.update(attrs);modules[name]=o
        adapter_calls=[];run_calls=[]
        def select_adapter(app,row,cfg,gate):
            adapter_calls.append((row['target_role'] if row else '', cfg))
            return 'CODEX' if row and row['target_role']=='developer' else 'DOT'
        async def execute(invocation_id, **kwargs):
            run_calls.append((invocation_id,kwargs.get('adapter'),kwargs.get('on_preclaim_deferred')))
            assert callable(kwargs['on_preclaim_deferred'])
            kwargs['on_preclaim_deferred']()
            return {'receipt':'fixture-only'}
        def wakeup(app):
            return types.SimpleNamespace(set=lambda:counts.__setitem__('wakeups',counts['wakeups']+1))
        async def advance(app, invocation_id, receipt):
            assert receipt=={'receipt':'fixture-only'}
            counts['advances']+=1
        module('open_webui');module('open_webui.utils')
        module('open_webui.utils.agentic_executor',execute=execute)
        module('open_webui.utils.agentic_execution_router',select_adapter=select_adapter)
        rows={'DEV':{'target_role':'developer'},'QA':{'target_role':'qa'},
              'IT':{'target_role':'it_manager','work_packet':{}},
              'NATIVE':{'target_role':'it_manager','work_packet':{'native':True}}}
        module('open_webui.utils.agentic_governance',find=lambda invocation_id,include_private=False: rows[invocation_id] if include_private else None)
        module('open_webui.utils.agentic_it_execution',ITAdapter=lambda *args:'IT')
        module('open_webui.utils.agentic_native_verification',selected_packet=lambda packet: bool(packet.get('native')))
        module('open_webui.utils.agentic_native_execution',NativeVerificationAdapter=lambda *args:'NATIVE')
        namespace={'_config':lambda:{'fixture':'config'},'_gate':object(),
                   '_advance':advance,'_reconciliation_wakeup':wakeup,'json':json}
        exec(compile(ast.Module(body=[deliver],type_ignores=[]),str(ROUTER),'exec'),namespace)
        with patch.dict(sys.modules,modules):
            for ident in ('DEV','QA','IT','NATIVE'):
                asyncio.run(namespace['_deliver'](object(),ident))
        self.assertEqual([x[1] for x in run_calls],['CODEX','DOT','IT','NATIVE'])
        self.assertTrue(all(callable(x[2]) for x in run_calls))
        self.assertEqual([x[0] for x in adapter_calls],['developer','qa'])
        self.assertEqual(counts['wakeups'],4)
        self.assertEqual(counts['advances'],4)

if __name__=='__main__':unittest.main(verbosity=2)
