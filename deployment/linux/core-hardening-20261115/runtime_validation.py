"""Pure, bounded validation for typed runtime leaves and the fixed contract pair."""
import json
import math
from pathlib import PurePosixPath

MAX_RUNTIME_BYTES = 2_000_000
JSON_SCHEMA_PATH = 'utils/agentic_contracts/schemas/governance_v1.schema.json'
JSON_SCHEMA_DRAFT = 'https://json-schema.org/draft/2020-12/schema'
JSON_SCHEMA_ID = 'https://abit.ai/schemas/governance_v1.schema.json'


def require(value, code):
    if not value:
        raise RuntimeError(code)


class RuntimePython:
    name = 'RuntimePython'

    @classmethod
    def validate(cls, raw, relative_path):
        try:
            compile(raw, relative_path, 'exec')
        except (SyntaxError, UnicodeError, ValueError, OverflowError):
            raise RuntimeError('INVALID_RUNTIME_PYTHON') from None


class RuntimeJSON:
    name = 'RuntimeJSON'

    @staticmethod
    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'DUPLICATE_RUNTIME_JSON_KEY')
            value[key] = item
        return value

    @staticmethod
    def constant(value):
        raise RuntimeError('NONFINITE_RUNTIME_JSON_VALUE')

    @classmethod
    def validate(cls, raw, relative_path):
        require(relative_path == JSON_SCHEMA_PATH, 'UNREVIEWED_RUNTIME_JSON_PATH')
        try:
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=cls.object_pairs,
                               parse_constant=cls.constant)
        except (ValueError, UnicodeError, RecursionError):
            raise RuntimeError('INVALID_RUNTIME_JSON') from None
        require(isinstance(value, dict) and value.get('$schema') == JSON_SCHEMA_DRAFT
                and value.get('$id') == JSON_SCHEMA_ID and isinstance(value.get('$defs'), dict),
                'EXACT_RUNTIME_JSON_SCHEMA_REQUIRED')
        # This validates schema structure only. Core remains the business validator.
        # Every schema reference stays local; validation never resolves network URLs.
        pending = [value]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                for key, item in node.items():
                    if key in {'$ref', '$dynamicRef'}:
                        require(isinstance(item, str) and
                                (item == '#' or item.startswith('#/')),
                                'EXTERNAL_RUNTIME_JSON_REFERENCE_DENIED')
                    pending.append(item)
            elif isinstance(node, list):
                pending.extend(node)
            elif isinstance(node, float):
                require(math.isfinite(node), 'NONFINITE_RUNTIME_JSON_VALUE')
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError:
            raise RuntimeError('INVALID_RUNTIME_JSON_SCHEMA') from None


def runtime_kind(relative_path):
    require(isinstance(relative_path, str) and relative_path
            and PurePosixPath(relative_path).as_posix() == relative_path
            and not PurePosixPath(relative_path).is_absolute()
            and all(part not in {'.', '..'} for part in PurePosixPath(relative_path).parts),
            'CANONICAL_RUNTIME_RELATIVE_PATH_REQUIRED')
    if relative_path == JSON_SCHEMA_PATH:
        return RuntimeJSON
    require(PurePosixPath(relative_path).suffix == '.py', 'UNREVIEWED_RUNTIME_KIND')
    return RuntimePython


def validate_runtime(relative_path, raw, expected_kind=None):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_RUNTIME_BYTES,
            'BOUNDED_RUNTIME_BYTES_REQUIRED')
    kind = runtime_kind(relative_path)
    require(expected_kind is None or expected_kind == kind.name,
            'RUNTIME_KIND_MISMATCH')
    kind.validate(raw, relative_path)
    return kind.name


ALREADY_CURRENT_CONTRACTS = frozenset({
    'utils/agentic_contracts/developer_delivery.py',
    JSON_SCHEMA_PATH,
})


def replacement_summary(files):
    """Allow only the complete already-current capacity pair as selected inputs."""
    require(isinstance(files, list) and len(files) == 14
            and all(isinstance(item, dict) and isinstance(item.get('relative_path'), str)
                    for item in files)
            and len({item['relative_path'] for item in files}) == 14,
            'EXACT_TYPED_REPLACEMENT_SCOPE_REQUIRED')
    require(all(type(item.get('replacement_required')) is bool
                and type(item.get('new_file')) is bool for item in files)
            and sum(item['new_file'] for item in files) == 2,
            'EXACT_BOOLEAN_REPLACEMENT_CLASSIFICATION_REQUIRED')
    current = {item['relative_path'] for item in files if not item['replacement_required']}
    require(not current or current == ALREADY_CURRENT_CONTRACTS,
            'EXACT_ALREADY_CURRENT_CONTRACT_PAIR_REQUIRED')
    for item in files:
        if not item['replacement_required']:
            pin = item.get('candidate_sha256')
            meta = item.get('live_metadata')
            require(item['new_file'] is False and isinstance(pin, str) and len(pin) == 64
                    and all(char in '0123456789abcdef' for char in pin)
                    and item.get('original_sha256') == pin and isinstance(meta, dict)
                    and type(item.get('candidate_bytes')) is int and item['candidate_bytes'] > 0
                    and type(meta.get('size')) is int and meta['size'] == item['candidate_bytes'],
                    'EXACT_ALREADY_CURRENT_BYTES_AND_SIZE_REQUIRED')
        elif not item['new_file']:
            require(item.get('original_sha256') != item.get('candidate_sha256'),
                    'ALREADY_CURRENT_INPUT_MUST_NOT_BE_REWRITTEN')
    return {'replacement_count': 14 - len(current),
            'existing_replacement_count': 12 - len(current),
            'unselected_original_count': 135,
            'preserved_original_count': 135 + len(current),
            'already_current_selected_count': len(current),
            'already_current_selected_paths': sorted(current)}
