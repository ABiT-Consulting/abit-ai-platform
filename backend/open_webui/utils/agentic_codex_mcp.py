"""Minimal stdio MCP client for one private, run-bound local ABIT gateway.

No ERP credentials, executable commands, arbitrary URLs or filesystem tools.
The controller owns the socket and authenticates a per-process random capability.
"""
import asyncio
import json
import os
import sys

MAX = 1_000_000
TOOL = {
    'name': 'agentic_action',
    'description': ('Execute a scoped ABIT operation. First call context.read. '
                    'Use stable call_id values; never repeat an uncertain action with a new ID. '
                    'checkpoint.save saves verified progress. relay.assist delegates advisory reasoning.'),
    'inputSchema': {
        'type': 'object', 'properties': {
            'call_id': {'type': 'string', 'minLength': 1, 'maxLength': 160},
            'operation': {'type': 'string', 'minLength': 1, 'maxLength': 100},
            'arguments': {'type': 'object', 'additionalProperties': True}},
        'required': ['call_id', 'operation', 'arguments'], 'additionalProperties': False}}


async def forward(arguments):
    reader, writer = await asyncio.open_unix_connection(os.environ['ABIT_CODEX_SOCKET'], limit=MAX)
    try:
        writer.write((json.dumps({'capability': os.environ['ABIT_CODEX_CAPABILITY'], **arguments}) + '\n').encode())
        await writer.drain()
        raw = await reader.readline()
        if len(raw) > MAX or not raw:
            raise ValueError('Local gateway response unavailable; preserve original call ID')
        return json.loads(raw)
    finally:
        writer.close()
        await writer.wait_closed()


def main():
    for raw in sys.stdin.buffer:
        request = {}
        try:
            if len(raw) > MAX:
                raise ValueError('MCP request exceeds bound')
            request = json.loads(raw)
            if 'id' not in request:
                continue
            method = request.get('method')
            if method == 'initialize':
                result = {'protocolVersion': request.get('params', {}).get('protocolVersion', '2024-11-05'),
                          'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'abit-governed-codex', 'version': '1.0.0'}}
            elif method == 'tools/list':
                result = {'tools': [TOOL]}
            elif method == 'ping':
                result = {}
            elif method == 'tools/call':
                params = request.get('params', {})
                if params.get('name') != TOOL['name']:
                    raise ValueError('Unknown tool')
                value = asyncio.run(forward(params.get('arguments', {})))
                result = {'content': [{'type': 'text', 'text': json.dumps(value)}],
                          'isError': bool(value.get('error'))}
            else:
                raise ValueError('Unsupported MCP method')
            response = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        except Exception as error:
            response = {'jsonrpc': '2.0', 'id': request.get('id'),
                        'error': {'code': -32000, 'message': str(error)[:250]}}
        print(json.dumps(response), flush=True)


if __name__ == '__main__':
    main()
