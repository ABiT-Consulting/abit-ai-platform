"""Exact authoritative specialist routing; Developer PR gates remain separate."""
import json

async def verify_infrastructure(args,**kwargs):
    from open_webui.utils.agentic_executor import implementation_source
    row,packet=kwargs['row'],kwargs['packet']
    source=implementation_source(row,packet,kwargs.get('db_path'))
    if source['target_role']!='it_manager':raise ValueError('Infrastructure verification requires an authoritative IT predecessor')
    operation=(json.loads(source['work_packet']).get('tool_action') or {}).get('operation')
    if operation in {'inspect','cleanup_disposable'}:
        from open_webui.utils.agentic_it_routine_qa import verify_original
    else:
        from open_webui.utils.agentic_it_qa import verify_original
    return await verify_original(args,**kwargs)

async def assert_specialist_qa(row,packet,result,config,db_path=None,*,bridge=None):
    from open_webui.utils.agentic_executor import implementation_source
    source=implementation_source(row,packet,db_path)
    if source['target_role']=='operations':
        from open_webui.utils.agentic_operations_qa import assert_operations_qa_result
        return await assert_operations_qa_result(row,packet,result,config,db_path,bridge=bridge)
    if source['target_role']=='it_manager':
        operation=(json.loads(source['work_packet']).get('tool_action') or {}).get('operation')
        if operation in {'inspect','cleanup_disposable'}:
            from open_webui.utils.agentic_it_routine_qa import assert_routine_qa_result
            return await assert_routine_qa_result(row,packet,result,config,db_path,bridge=bridge)
        from open_webui.utils.agentic_it_qa import assert_it_qa_result
        return await assert_it_qa_result(row,packet,result,config,db_path,bridge=bridge)
    return False
