from pathlib import Path
import ast,datetime,difflib,hashlib,json,shutil

ROOT=Path('/srv/abit-dev-linux/abit-ai-gateway-preflight-diagnostics-20261122')
LIVE=Path('/srv/abit-dev-linux/abit-ai/current/runtime/open_webui')
PACKAGE=ROOT/'deployment/gateway-preflight-diagnostics-20261122'
PACKAGE.mkdir(parents=True,exist_ok=True)
sha=lambda raw:hashlib.sha256(raw).hexdigest()
expected={
 'utils/agentic_transport_retry.py':'8ac1970ddd09e60e42fb93620ade69a67cb4ca1bb8076d951cb0ea01366fdf9f',
 'utils/agentic_relay_scope.py':'c3cdd4d3dbb5143ec594c7f9422aae725b1ae192287ecfb7e269fe422d4ff6ea',
 'routers/agentic_governance.py':'85dbbe0a6d5ead08796b830eba212541016f1f790630ff6ddbcf8829bf6edefc',
}
before={name:(LIVE/name).read_bytes() for name in expected}
assert all(sha(before[name])==value for name,value in expected.items())
candidate=Path('/srv/abit-dev-linux/tmp/intermarket-pr23-pr25-release-preparation-20261122/core-minimal/agentic_transport_retry.candidate.py').read_bytes()
assert sha(candidate)=='bf813b34e602017086db6859574e60d7c3e094c6ffb83442a42e75ab78dcbfc3'
scope=before['utils/agentic_relay_scope.py'].decode()
helper=r'''
# These diagnostics are observations only. They never authorize admission,
# retry, fallback or a changed invocation state. Never retain response text.
MAX_ERROR_BYTES = 4096
PROOF_FAILURE_PREFIX = 'Gateway preflight denied: '
_CORE_PROOF_FAILURES = {
    'Gateway invocation is not registered': ('core_invocation', 'INVOCATION_UNREGISTERED'),
    'Gateway invocation belongs to a different registered owner': ('core_owner', 'REGISTERED_OWNER_MISMATCH'),
    'Gateway invocation is not eligible for admission or work': ('core_state', 'INVOCATION_INELIGIBLE'),
    'ERP Project no longer authorizes governed actions': ('core_project', 'PROJECT_AUTHORITY_DENIED'),
    'ERP work packet changed before the gateway operation': ('core_work_packet', 'WORK_PACKET_CHANGED'),
    'Gateway scope differs from the original server-owned work packet': ('core_scope', 'SCOPE_BINDING_CHANGED'),
    'The existing owner/account has no authenticated action gateway profile': ('core_profile', 'OWNER_PROFILE_UNAVAILABLE'),
    'The existing MCP connection has not been verified for this owner': ('core_profile', 'MCP_CONNECTION_UNVERIFIED'),
    'The action gateway is not linked to the registered abitdot': ('core_profile', 'DOT_BINDING_MISMATCH'),
    'ERP has not verified its actual loaded managed-resource guards': ('core_native_boundary', 'NATIVE_BOUNDARY_UNVERIFIED'),
    'No real gateway operation registry is installed': ('core_registry', 'OPERATION_REGISTRY_UNAVAILABLE'),
    'The packet requires an unsupported gateway capability': ('core_registry', 'TOOL_SCOPE_UNSUPPORTED'),
}
_CORE_CODES = {code: stage for stage, code in _CORE_PROOF_FAILURES.values()}
_CORE_CODES['CORE_AUTHORITY_UNVERIFIED'] = 'core_authority'
_LOCAL_STAGES = {
    'initialization': 'PREFLIGHT_UNAVAILABLE',
    'input_binding': 'INPUT_BINDING_INVALID',
    'registered_owner': 'REGISTERED_OWNER_UNVERIFIED',
    'work_packet_validation': 'WORK_PACKET_INVALID',
    'primary_dot': 'PRIMARY_DOT_UNAVAILABLE',
    'tool_scopes': 'TOOL_SCOPES_INVALID',
    'relay_account': 'RELAY_ACCOUNT_UNVERIFIED',
    'relay_endpoint': 'RELAY_ENDPOINT_UNAVAILABLE',
    'relay_credentials': 'RELAY_CREDENTIAL_UNAVAILABLE',
    'scope_binding': 'SCOPE_BINDING_INVALID',
    'relay_request': 'RELAY_TRANSPORT_UNAVAILABLE',
    'relay_response': 'RELAY_HTTP_REJECTED',
    'proof_body': 'PROOF_BODY_INVALID',
    'proof_validation': 'PROOF_INVALID',
}
_RESPONSE_CODES = {
    'RELAY_HTTP_REJECTED': 'relay_response',
    'RELAY_AUTH_REQUIRED': 'relay_response',
    'RELAY_NOT_CONFIGURED': 'relay_response',
    'RELAY_SCOPE_INVALID': 'relay_response',
    'CORE_RESPONSE_UNAVAILABLE': 'core_transport',
    'CORE_CREDENTIAL_UNAVAILABLE': 'core_transport',
    'CORE_PROOF_INVALID': 'core_proof',
    'CORE_HTTP_REJECTED': 'core_response',
    'SCOPE_DIGEST_MISMATCH': 'core_scope',
    'PROOF_BODY_OVERSIZED': 'proof_body',
}
_RELAY_MESSAGES = {
    'Governed gateway request could not be verified': 'RELAY_SCOPE_INVALID',
    'Governed gateway Core response is unavailable; preserve the original request': 'CORE_RESPONSE_UNAVAILABLE',
    'Gateway server credential file is unavailable': 'CORE_CREDENTIAL_UNAVAILABLE',
    'Gateway scope proof does not authorize this original job': 'CORE_PROOF_INVALID',
}


def gateway_proof_failure_detail(error):
    """Fixed proof-only error detail; no upstream exception text is returned."""
    message = error.args[0] if isinstance(error, ValueError) and len(error.args) == 1 and type(error.args[0]) is str else None
    code = _CORE_PROOF_FAILURES.get(message, ('core_authority', 'CORE_AUTHORITY_UNVERIFIED'))[1]
    return PROOF_FAILURE_PREFIX + code


def _safe_http_status(value):
    return value if type(value) is int and 100 <= value <= 599 else None


def _preflight_failure(stage, *, error_code=None, http_status=None, core_http_status=None):
    allowed = {**{code: key for key, code in _LOCAL_STAGES.items()}, **_CORE_CODES, **_RESPONSE_CODES}
    code = error_code or _LOCAL_STAGES.get(stage, 'PREFLIGHT_UNAVAILABLE')
    stage = allowed.get(code, 'initialization')
    if code not in allowed:
        code = 'PREFLIGHT_UNAVAILABLE'
    diagnostic = {
        'stage': stage,
        'http_status': _safe_http_status(http_status),
        'core_http_status': _safe_http_status(core_http_status),
        'error_code': code,
    }
    error = CapabilityUnavailable(
        BLOCKER + '; gateway_preflight=' + json.dumps(diagnostic, sort_keys=True, separators=(',', ':'))
    )
    error.gateway_preflight_diagnostic = diagnostic
    return error


def _http_preflight_diagnostic(raw, status):
    """Parse only bounded fixed diagnostics from the authenticated relay."""
    result = {'stage': 'relay_response', 'error_code': 'RELAY_HTTP_REJECTED',
              'http_status': _safe_http_status(status), 'core_http_status': None}
    if len(raw) > MAX_ERROR_BYTES:
        return result
    try:
        value = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return result
    if not isinstance(value, dict):
        return result
    if status == 403 and value.get('code') == 'DOT_AUTH_REQUIRED':
        result['error_code'] = 'RELAY_AUTH_REQUIRED'
    elif status == 424 and value.get('code') == 'DOT_NOT_CONFIGURED':
        result['error_code'] = 'RELAY_NOT_CONFIGURED'
    message = value.get('message')
    if not isinstance(message, str) or len(message) > 500:
        return result
    if status == 424 and message in _RELAY_MESSAGES:
        result['error_code'] = _RELAY_MESSAGES[message]
        return result
    match = re.fullmatch(r'Core HTTP ([45][0-9]{2}): (.+)', message)
    if status != 424 or not match:
        return result
    result['core_http_status'] = int(match[1])
    result['error_code'] = 'CORE_HTTP_REJECTED'
    detail = match[2]
    if detail == 'Gateway scope fingerprint does not match':
        result['error_code'] = 'SCOPE_DIGEST_MISMATCH'
    elif detail.startswith(PROOF_FAILURE_PREFIX):
        code = detail[len(PROOF_FAILURE_PREFIX):]
        if code in _CORE_CODES:
            result['error_code'] = code
    return result

'''
anchor="\n\ndef _unavailable():\n"
assert scope.count(anchor)==1
scope=scope.replace(anchor,"\n"+helper+anchor,1)
start=scope.index('async def require_relay_scope(')
prefix,body=scope[:start],scope[start:]
def once(old,new):
 global body
 assert body.count(old)==1,old
 body=body.replace(old,new,1)
once('    try:\n        import aiohttp','    stage = \'initialization\'\n    diagnostic = None\n    http_status = None\n    try:\n        import aiohttp')
once("        binding = packet['binding']","        stage = 'input_binding'\n        binding = packet['binding']")
once("        project_roster = ledger.roster(row['erp_project'], db_path)","        stage = 'registered_owner'\n        project_roster = ledger.roster(row['erp_project'], db_path)")
once('        if validate_work_packet(\n',"        stage = 'work_packet_validation'\n        if validate_work_packet(\n")
once('        dot_id = primary_dot_for_owner(owner_id)',"        stage = 'primary_dot'\n        dot_id = primary_dot_for_owner(owner_id)")
once("        scopes = packet['tool_scopes']","        stage = 'tool_scopes'\n        scopes = packet['tool_scopes']")
once('        account = await relay_account_for_owner(owner_id)',"        stage = 'relay_account'\n        account = await relay_account_for_owner(owner_id)")
once("        base = validated_local_relay_url(os.getenv('ABIT_DOT_PRIMARY_RELAY_URL') or MESSAGE_URL)","        stage = 'relay_endpoint'\n        base = validated_local_relay_url(os.getenv('ABIT_DOT_PRIMARY_RELAY_URL') or MESSAGE_URL)")
once("        selected = os.getenv('ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE')","        stage = 'relay_credentials'\n        selected = os.getenv('ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE')")
once('        scope = scope_for_packet(row, packet, owner_id, account, dot_id, actual_run)',"        stage = 'scope_binding'\n        scope = scope_for_packet(row, packet, owner_id, account, dot_id, actual_run)")
once('        async with aiohttp.ClientSession(', "        stage = 'relay_request'\n        async with aiohttp.ClientSession(")
once("                if response.status != 200:\n                    raise _unavailable()\n                raw = await response.content.read(MAX_PROOF_BYTES + 1)\n                if len(raw) > MAX_PROOF_BYTES:\n                    raise _unavailable()\n                proof = json.loads(raw)\n        return _proof(proof, scope, now=int(time.time()))",
"""                http_status = _safe_http_status(response.status)
                stage = 'relay_response'
                if response.status != 200:
                    raw = await response.content.read(MAX_ERROR_BYTES + 1)
                    diagnostic = _http_preflight_diagnostic(raw, response.status)
                    raise _unavailable()
                stage = 'proof_body'
                raw = await response.content.read(MAX_PROOF_BYTES + 1)
                if len(raw) > MAX_PROOF_BYTES:
                    diagnostic = {'stage': stage, 'error_code': 'PROOF_BODY_OVERSIZED',
                                  'http_status': http_status}
                    raise _unavailable()
                proof = json.loads(raw)
        stage = 'proof_validation'
        return _proof(proof, scope, now=int(time.time()))""")
once('    except CapabilityUnavailable:\n        raise\n    except Exception:\n        raise _unavailable() from None',
"""    except CapabilityUnavailable:
        raise _preflight_failure(**(diagnostic or {'stage': stage, 'http_status': http_status})) from None
    except Exception:
        raise _preflight_failure(stage, http_status=http_status) from None""")
scope=prefix+body

router=before['routers/agentic_governance.py'].decode()
old="""    except CapabilityUnavailable as error:
        raise HTTPException(424, str(error)) from None
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(409, _erp_failure(error, 'Gateway authority validation failed')) from None
"""
new="""    except CapabilityUnavailable as error:
        if operation == 'proof':
            from open_webui.utils.agentic_relay_scope import gateway_proof_failure_detail

            raise HTTPException(424, gateway_proof_failure_detail(error)) from None
        raise HTTPException(424, str(error)) from None
    except HTTPException:
        raise
    except Exception as error:
        if operation == 'proof':
            from open_webui.utils.agentic_relay_scope import gateway_proof_failure_detail

            raise HTTPException(409, gateway_proof_failure_detail(error)) from None
        raise HTTPException(409, _erp_failure(error, 'Gateway authority validation failed')) from None
"""
assert router.count(old)==1
router=router.replace(old,new,1)
old="""async def gateway_proof(body: GatewayProofInput, x_abit_erp_control: str = Header(default='')):
    _auth(x_abit_erp_control)
"""
new="""async def gateway_proof(body: GatewayProofInput, x_abit_erp_control: str = Header(default='')):
    \"\"\"Negotiate read-only scope without admission or provider execution.

    Failed checks return fixed diagnostic codes without upstream response,
    packet or credential contents; all existing authority checks still apply.
    \"\"\"
    _auth(x_abit_erp_control)
"""
assert router.count(old)==1
router=router.replace(old,new,1)
after={'utils/agentic_transport_retry.py':candidate,
       'utils/agentic_relay_scope.py':scope.encode(),
       'routers/agentic_governance.py':router.encode()}
rows=[];forward=[];rollback=[]
for name,raw in after.items():
 ast.parse(raw)
 for folder,value in [('preimages',before[name]),('runtime',raw)]:
  p=PACKAGE/folder/'open_webui'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(value)
 target=ROOT/'backend/open_webui'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
 forward.extend(difflib.unified_diff(before[name].decode().splitlines(True),raw.decode().splitlines(True),fromfile='a/open_webui/'+name,tofile='b/open_webui/'+name))
 rollback.extend(difflib.unified_diff(raw.decode().splitlines(True),before[name].decode().splitlines(True),fromfile='a/open_webui/'+name,tofile='b/open_webui/'+name))
 rows.append({'path':name,'before_sha256':sha(before[name]),'after_sha256':sha(raw),'bytes':len(raw)})
(PACKAGE/'forward.patch').write_text(''.join(forward))
(PACKAGE/'rollback.patch').write_text(''.join(rollback))
manifest={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'source_base_commit':'b25b1b0790f73b2071e0f20a4038b7bb90478ed0',
          'runtime_root':str(LIVE),'resolved_runtime':str(LIVE.resolve()),
          'runtime_files':rows,'scope':'Reviewed accepted-original Retry guard plus fixed proof-only diagnostics; no live state or authority changes',
          'forward_patch_sha256':sha((PACKAGE/'forward.patch').read_bytes()),
          'rollback_patch_sha256':sha((PACKAGE/'rollback.patch').read_bytes()),
          'production_deployed':False}
(PACKAGE/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
print(json.dumps({'package':str(PACKAGE),'manifest_sha256':sha((PACKAGE/'manifest.json').read_bytes()),'files':rows}))
