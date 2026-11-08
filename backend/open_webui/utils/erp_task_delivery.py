"""Completion proof for an independently tested ERP development candidate."""

import base64
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import urlsplit


DEV_DELIVERY_FIELD = 'custom_ai_dev_delivery_required'
GIT_OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
SHA256 = re.compile(r'[0-9a-f]{64}')
AGENT = re.compile(r'(?:erp-[a-f0-9]{32}|[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12})')
SMOKE_CHECK = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')


@dataclass(frozen=True)
class DeliveryDecision:
    blocker: str | None = None
    proof: dict | None = None
    awaiting_receipt: bool = False


def canonical_json(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), sort_keys=True, allow_nan=False).encode('ascii')


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate key in delivery receipt')
        value[key] = item
    return value


def strict_match(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def seconds(value):
    return type(value) in (int, float) and 0 < value < 1e11 and math.isfinite(value)


def https_target(value):
    if not isinstance(value, str) or re.search(r'\s', value):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == 'https' and bool(url.hostname) and url.netloc == url.hostname
                and not url.query and not url.fragment)
    except ValueError:
        return False


def read_bounded(path, limit):
    # Refuse symlinks and special files. An incomplete writer cannot hold up
    # the resolver. Publishers replace a complete receipt atomically.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError('Expected a regular delivery proof file')
        data = file.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Delivery proof file exceeds its size limit')
    return data


def signed_payload(path, public_key_path):
    # Only the isolated publisher holds the private key. The runtime can
    # validate evidence even if someone with receipt-directory access modifies it.
    from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    envelope = json.loads(read_bounded(path, 16384), object_pairs_hook=unique_object)
    if (not isinstance(envelope, dict) or set(envelope) != {'receipt', 'signature'}
            or not isinstance(envelope['receipt'], dict) or not isinstance(envelope['signature'], str)):
        raise ValueError('Invalid delivery receipt envelope')
    receipt = envelope['receipt']
    signature = base64.b64decode(envelope['signature'], validate=True)
    try:
        public_key = load_pem_public_key(read_bounded(public_key_path, 4096))
    except UnsupportedAlgorithm as error:
        raise ValueError('Unsupported delivery publisher key') from error
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError('Delivery publisher must use an Ed25519 public key')
    try:
        public_key.verify(signature, canonical_json(receipt))
    except InvalidSignature as error:
        raise ValueError('Invalid delivery receipt signature') from error
    return receipt


def evaluate_delivery(*, task, result, attempt_id, automation_id, config=None, now=None):
    """Return a review reason, or evidence sufficient to complete the task.

    ``result`` must be the authenticated CLI supervisor response. An ERP
    flag can require delivery, but only operator policy selects a target and
    trusts a release publisher. Agent text is never deployment evidence.
    """
    evidence = result.get('evidence') or {}
    if not isinstance(evidence, dict):
        evidence = {}
    candidate, tester = evidence.get('candidate'), evidence.get('tester')
    if (result.get('status') != 'completed' or result.get('verified') is not True
            or result.get('testing_passed') is not True or not isinstance(tester, dict)
            or tester.get('passed') is not True or not isinstance(tester.get('checks'), list)
            or not any(isinstance(check, str) and check.strip() for check in tester['checks'])):
        return DeliveryDecision('Independent testing did not prove the candidate passed.')
    if (not isinstance(candidate, dict) or set(candidate) != {'base', 'tree'}
            or not strict_match(GIT_OID, candidate['base']) or not strict_match(GIT_OID, candidate['tree'])
            or len(candidate['base']) != len(candidate['tree'])):
        return DeliveryDecision('Independent tests passed, but the worker did not attest the exact Git candidate.')

    task_id = task.get('name')
    if not isinstance(task_id, str) or not task_id:
        return DeliveryDecision('The tested candidate cannot be matched to an ERP task.')
    configured = (config or {}).get('delivery_tasks', {}) if config is None or isinstance(config, dict) else None
    if not isinstance(configured, dict):
        return DeliveryDecision('DEV delivery policy is invalid; review the candidate and delivery requirements.')
    required_flag = task.get(DEV_DELIVERY_FIELD)
    if (task_id not in configured
            and required_flag in (None, False, 0, '', '0')):
        return DeliveryDecision(proof={'candidate': dict(candidate), 'delivery_required': False})

    overrides = configured.get(task_id, {})
    if not isinstance(overrides, dict):
        return DeliveryDecision('DEV delivery policy for this task is invalid.')
    policy = overrides
    target = policy.get('target')
    if not https_target(target):
        return DeliveryDecision('Independent tests passed; no approved HTTPS DEV target is configured.')
    review = f'Independent tests passed for the candidate; DEV delivery at {target} remains unverified.'
    smoke_check = policy.get('smoke_check', 'independent-target-smoke')
    if not strict_match(SMOKE_CHECK, smoke_check):
        return DeliveryDecision(review + ' No valid independent DEV smoke check is configured.')
    receipt_directory, public_key = policy.get('receipt_directory'), policy.get('public_key_file')
    if not all(isinstance(path, str) and Path(path).is_absolute() for path in (receipt_directory, public_key)):
        return DeliveryDecision(review + ' No trusted release publisher is configured.')
    if not strict_match(SHA256, attempt_id) or not strict_match(AGENT, automation_id):
        return DeliveryDecision(review + ' The run identity is invalid.')
    try:
        receipt = signed_payload(Path(receipt_directory) / (attempt_id + '.json'), Path(public_key))
    except FileNotFoundError:
        ready = Path(public_key).is_file() and Path(receipt_directory).is_dir()
        return DeliveryDecision(review + ' The signed release receipt or publisher key is unavailable.',
                                awaiting_receipt=ready)
    except (ImportError, OSError, ValueError, RecursionError):
        return DeliveryDecision(review + ' The trusted release receipt could not be validated.')

    expected = {'schema': 'abit.erp.dev-delivery.v1', 'task': task_id, 'attempt': attempt_id,
                'automation': automation_id, 'candidate': candidate, 'target': target,
                'smoke_check': smoke_check}
    if any(receipt.get(field) != value for field, value in expected.items()):
        return DeliveryDecision(review + ' The signed receipt refers to a different candidate, run, target, or check.')
    artifact = receipt.get('artifact_sha256')
    if not strict_match(SHA256, artifact) or receipt.get('observed_artifact_sha256') != artifact:
        return DeliveryDecision(review + ' The running artifact could not be matched to the released artifact.')
    if receipt.get('smoke_passed') is not True:
        return DeliveryDecision(review + ' The independent DEV smoke check did not pass.')
    tested_at, deployed_at, verified_at = (result.get('finished_at'), receipt.get('deployed_at'), receipt.get('verified_at'))
    now = time.time() if now is None else now
    if (not all(seconds(value) for value in (tested_at, deployed_at, verified_at, now))
            or not tested_at <= deployed_at <= verified_at <= now + 30):
        return DeliveryDecision(review + ' The receipt does not prove DEV was tested after this candidate was released.')
    return DeliveryDecision(proof={
        'candidate': dict(candidate), 'delivery_required': True, 'target': target,
        'artifact_sha256': artifact, 'smoke_check': expected['smoke_check'], 'verified_at': verified_at,
    })
