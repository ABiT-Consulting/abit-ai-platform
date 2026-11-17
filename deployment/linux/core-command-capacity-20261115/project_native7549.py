"""Pure, exact-7549 evidence projection for the existing Core capacity contract.

No file reads/writes, imports of runtime/controller code, clock, network, gate,
database, lifecycle, publication or model operations occur in this adapter.
Root-private retention and the complete release plan remain separate work.
"""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath

CONTROLLER_SHA256 = 'f9807a7f3277c9b6fa9b790ecf2b5064b9f4001c1549a8d3b8b5ad050cd13fb7'
HISTORY_HELPER_SHA256 = '1d1804e48c1a23fe6f86ee796e5756bd1c16a44376848e0a83788d79002b2052'
CORE_READ_HELPER_SHA256 = 'f6477ec6b7173a7c7dd07a7423c037ea624ca759dc6383d2353f64bbcdd2a0b3'
INPUT_SHA256 = {
    'publication': '9d2cb9b354e68474c9b5a3a7fb07e85b21a52cb282797436e342b63535ebcbd2',
    'channel': 'b7ffd21cd2199f1d28f60656e87dc9d3535257c5b5bfcc16a060b7d46a4aff0b',
    'observer': '94e2fe1d9cbfbbbf1e848ea1d784e87d685f999972d0f3b6dda5b50e99451931',
    'worker_review': '6069be6d9d3d73f576bdb33d2e98b255ef06d09141fdccdc136a05eba235efca',
    'readiness': '94db17098643e8112eb9c52f00cb4935cb9865f343ba8210e36e6a685728584b',
}
FILENAMES = {
    'publication': 'native-publication.original7549.json',
    'channel': 'channel-new-start.original7549.json',
    'observer': 'observer.original7549.json',
    'worker_review': 'native-worker-history.review.json',
    'readiness': 'core-readiness.review.json',
}
ROLE_MAPPING = (
    ('backend', 'backend'),
    ('agentic-governance-worker', 'governance'),
    ('scheduler', 'scheduler'),
)
ARCHIVE_PARENT = PurePosixPath('/var/lib/abit-ai-erp-rollouts/core-command-capacity')
MAX_INPUT_BYTES = 65536


def need(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'


def strict_json(raw):
    need(type(raw) is bytes and len(raw) <= MAX_INPUT_BYTES, 'BOUNDED_EXACT_BYTES_REQUIRED')
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('NONFINITE_JSON_DENIED')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    need(type(value) is dict, 'EXACT_JSON_OBJECT_REQUIRED')
    return value


def fixed_contract(controller_source):
    """Evaluate only three data assignments from exact reviewed controller bytes."""
    need(type(controller_source) is bytes and len(controller_source) <= MAX_INPUT_BYTES
         and sha(controller_source) == CONTROLLER_SHA256, 'EXACT_CONTROLLER_SOURCE_REQUIRED')
    names = {'TARGETS', 'NATIVE_FIXED', 'CHANNEL_FIXED'}
    nodes = [node for node in ast.parse(controller_source).body
             if isinstance(node, ast.Assign) and len(node.targets) == 1
             and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names]
    need(len(nodes) == 3 and {node.targets[0].id for node in nodes} == names,
         'EXACT_CONTROLLER_CONSTANT_ASSIGNMENTS_REQUIRED')
    # The pinned source contains literal dictionaries plus TARGETS subscripting.
    scope = {'__builtins__': {}}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'reviewed_capacity_constants', 'exec'), scope)
    return deepcopy(scope['NATIVE_FIXED']), deepcopy(scope['CHANNEL_FIXED'])


def project(controller_source, inputs):
    """Return deterministic bytes and a binding fragment; never retain them."""
    fixed_native, fixed_channel = fixed_contract(controller_source)
    need(type(inputs) is dict and set(inputs) == set(INPUT_SHA256), 'EXACT_FIVE_INPUTS_REQUIRED')
    values = {}
    for name, expected in INPUT_SHA256.items():
        raw = inputs[name]
        need(type(raw) is bytes and len(raw) <= MAX_INPUT_BYTES and sha(raw) == expected,
             'EXACT_RETAINED_INPUT_REQUIRED')
        values[name] = strict_json(raw)
    publication, channel, observer, review, readiness = (
        values[name] for name in ('publication', 'channel', 'observer', 'worker_review', 'readiness'))
    need(all(type(publication.get(key)) is type(item) and publication[key] == item
             for key, item in fixed_native.items()), 'ORIGINAL_NATIVE_FIELDS_CHANGED')
    need(all(type(channel.get(key)) is type(item) and channel[key] == item
             for key, item in fixed_channel.items()), 'ORIGINAL_CHANNEL_FIELDS_CHANGED')
    need(channel == {key: value for key, value in observer.items()
                     if key not in {'native_contract_publication', 'observation_reconciliation'}}
         and publication['channel_new_start_receipt_sha256'] == INPUT_SHA256['channel']
         and observer['native_contract_publication']['sha256'] == INPUT_SHA256['publication'],
         'EXACT_ORIGINAL_RECEIPT_CHAIN_REQUIRED')
    need(publication['original_f33'] == readiness['retained_evidence_checks']['original_f33'],
         'ORIGINAL_F33_IDENTITY_CHANGED')
    need(readiness['history_qualification'] == review['history_limits']
         and review['history_limits']['history_unchanged_since7533'] is False
         and review['history_limits']['row_field_and_cause_attribution'] == 'unknown'
         and readiness['history_policy_adopted'] is False
         and readiness['activation_authority'] is False, 'HISTORICAL_LIMITS_MUST_REMAIN')
    coverage = readiness['coverage_provenance']
    need(coverage['worker_and_contract_review_sha256'] == INPUT_SHA256['worker_review']
         and coverage['loaded_contract'] == review['7545']['loaded_contract']
         and coverage['worker'] == review['7545']['worker']
         and coverage['scheduler_paused_at_7545'] == review['7545']['scheduler_remains_paused']
         and coverage['task_cache_ready_at_7545'] == review['7545']['cache']['ready'],
         'EXACT_RETAINED_WORKER_COVERAGE_REQUIRED')
    source_services = publication['services']
    need(type(source_services) is dict
         and set(source_services) == {source for source, _ in ROLE_MAPPING},
         'EXACT_NATIVE_SERVICE_ROLES_REQUIRED')
    services, mapping = {}, []
    declared = readiness['proposed_lossless_service_mapping']
    need(type(declared) is list and len(declared) == 3, 'EXACT_REVIEWED_ROLE_MAPPING_REQUIRED')
    for source_role, core_role in ROLE_MAPPING:
        row = source_services[source_role]
        need(type(row) is dict and set(row) == {
            'container_id', 'image_id', 'running', 'private_spec_sha256'}
             and row['running'] is True, 'EXACT_NATIVE_SERVICE_ROW_REQUIRED')
        projected = {'id': row['container_id'], 'image': row['image_id'], 'running': row['running']}
        match = [item for item in declared if item['source_role'] == source_role
                 and item['core_role'] == core_role]
        need(len(match) == 1 and match[0]['retained_fields'] == row
             and match[0]['proposed_core_fields'] == projected
             and match[0]['source_receipt_sha256'] == INPUT_SHA256['publication'],
             'EXACT_REVIEWED_ROLE_MAPPING_REQUIRED')
        services[core_role] = projected
        mapping.append({'source_role': source_role, 'core_role': core_role,
                        'source_fields': deepcopy(row), 'projected_fields': deepcopy(projected)})
    bundle_identity = sha(canonical({'controller_sha256': CONTROLLER_SHA256, 'inputs': INPUT_SHA256}))
    archive = ARCHIVE_PARENT / ('native7549-' + bundle_identity[:24])
    channel_path = str(archive / FILENAMES['channel'])
    aggregate_name = 'native-publication.core-projection.json'
    aggregate = deepcopy(publication)
    need('projection_provenance' not in aggregate, 'ALREADY_PROJECTED_INPUT_DENIED')
    aggregate['services'] = services
    aggregate['channel_new_start_receipt_path'] = channel_path
    aggregate['projection_provenance'] = {
        'schema_version': 1, 'purpose': 'exact-retained7549-native-to-core-projection',
        'controller_contract_source_sha256': CONTROLLER_SHA256,
        'original_inputs': {
            name: {'sha256': INPUT_SHA256[name], 'retained_path': str(archive / FILENAMES[name])}
            for name in INPUT_SHA256},
        'service_mapping': mapping,
        'native_original_channel_path': publication['channel_new_start_receipt_path'],
        'coverage_provenance': deepcopy(coverage),
        'history_qualification': deepcopy(review['history_limits']),
        'retained_readback_utc': deepcopy(readiness['retained_evidence_checks']['retained_readback_utc']),
        'current_native_observation': False,
        'history_policy_adopted': False,
        'gate_or_original_actions': 0,
        'activation_authority': False,
    }
    files = {FILENAMES[name]: raw for name, raw in inputs.items()}
    files[aggregate_name] = canonical(aggregate)
    binding = {
        'path': str(archive / aggregate_name), 'sha256': sha(files[aggregate_name]),
        'expected': {**fixed_native, 'services': deepcopy(services)},
        'channel_path': channel_path, 'channel_sha256': INPUT_SHA256['channel'],
        'channel_expected': fixed_channel,
    }
    files['native-publication.binding-fragment.json'] = canonical(binding)
    manifest = {
        'schema_version': 1, 'purpose': 'offline-native7549-core-retention-proposal',
        'state': 'PROJECTED_NOT_ROOT_RETAINED_NOT_RELEASE_FROZEN',
        'controller_contract_source_sha256': CONTROLLER_SHA256,
        'bundle_identity_sha256': bundle_identity, 'archive_directory': str(archive),
        'root_archive_created': False, 'release_plan_created': False,
        'activation_authority': False, 'native_or_core_actions': 0,
        'directory_requires': {'uid': 0, 'gid': 0, 'mode': 0o700, 'canonical_no_symlink': True},
        'files_require': {'uid': 0, 'gid': 0, 'mode': 0o600, 'nlink': 1,
                         'regular': True, 'canonical_no_symlink': True},
        'create_only_retention': {
            'existing_archive_or_file': 'HOLD_OBSERVE_ONLY_NO_OVERWRITE_OR_MERGE',
            'creation_helper': 'history_guard.private_new',
            'creation_helper_sha256': HISTORY_HELPER_SHA256,
            'private_readback_helper': 'apply_core_hardening.read(private=True)',
            'private_readback_helper_sha256': CORE_READ_HELPER_SHA256,
            'fsync_each_file_and_parent': True,
            'rehash_all_files_before_consumption': True,
            'postretention_metadata_and_readback_receipt_required': True,
        },
        'files': {name: {'sha256': sha(raw), 'bytes': len(raw)} for name, raw in files.items()},
        'unchanged_remaining_release_requirements': [
            'successful current native inventory and fresh verified native backup',
            'exact two-leaf candidate, tests, current Core cohort and original preservation baseline',
            'fresh authenticated administrative browser tasks proof',
            'reviewed frozen release plan and exact lock-held original/gate checks',
        ],
    }
    files['retention-manifest.json'] = canonical(manifest)
    return files, binding, manifest
