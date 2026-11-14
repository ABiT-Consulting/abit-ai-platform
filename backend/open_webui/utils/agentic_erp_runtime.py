"""Optional private ERP channel configuration; no implicit public fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path

_CHANNEL = None
_FINGERPRINT = None


def configured_channel():
    global _CHANNEL, _FINGERPRINT
    source = os.getenv('ABIT_ERP_TASK_RESOLUTION_CONFIG')
    if not source:
        if _CHANNEL is not None:
            raise ValueError('ERP channel configuration disappeared; controlled service reload is required')
        return None
    config = json.loads(Path(source).read_text())
    if not isinstance(config, dict):
        raise ValueError('ERP server configuration must be an object')
    value = config.get('agentic_erp_channel', {})
    if not isinstance(value, dict) or (value and type(value.get('enabled')) is not bool):
        raise ValueError('ERP channel enabled flag must be an explicit boolean')
    if value.get('enabled') is not True:
        if _CHANNEL is not None:
            raise ValueError('ERP channel configuration changed; controlled service reload is required')
        return None
    if set(value) != {'enabled', 'bridge_id', 'site', 'service_user'}:
        raise ValueError('Private ERP channel configuration requires its exact approved identity')
    if value['site'] != 'erp.abit.ai' or value['service_user'] != 'ai-developer@abitcons.com':
        raise ValueError('Private ERP channel is restricted to the approved ERP site and service user')
    from open_webui.env import DATA_DIR
    from open_webui.utils.agentic_erp_channel import ChannelProfile, ReverseERPChannel
    from open_webui.utils.agentic_bridge import digest

    path = Path(
        os.getenv('ABIT_ERP_CONTROL_TOKEN_FILE', str(Path(DATA_DIR).parent / 'config/erp-assignment/control-token'))
    )
    secret = path.read_text().strip()
    if len(secret) < 32:
        raise ValueError('Private ERP channel server credential is unavailable')
    signature = digest([value, secret])
    if _CHANNEL is not None:
        if _FINGERPRINT != signature:
            raise ValueError('Private ERP channel identity changed; controlled service reload is required')
        return _CHANNEL
    _CHANNEL = ReverseERPChannel(ChannelProfile(**value), secret, request_timeout=8)
    _FINGERPRINT = signature
    return _CHANNEL


def configured_transport():
    channel = configured_channel()
    return channel.transport() if channel is not None else None


def governed_client(token_file):
    """Select the private channel only for fixed governed RPC consumers."""
    import httpx
    from open_webui.utils.erp_task_resolution import ERPClient

    transport = configured_transport()
    if transport is None:
        return ERPClient(token_file)
    from open_webui.utils.erp_task_resolution import ERP

    # Preserve ERPClient response/error semantics without loading a local ERP
    # API credential. The native receiving client supplies its existing token.
    client = ERPClient.__new__(ERPClient)
    client.client = httpx.AsyncClient(
        base_url=ERP, timeout=8, follow_redirects=False, transport=transport, trust_env=False
    )
    return client
