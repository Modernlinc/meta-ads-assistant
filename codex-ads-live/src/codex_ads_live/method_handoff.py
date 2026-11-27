"""Carry the host's completed method output to the selected workspace and case."""
from pathlib import Path
from hashlib import sha256

from . import client_workspace as workspace
from .business_profile import SECRET_PATTERN
from .journal import digest
from .method_bundle import METHOD_ROLES, RESEARCH_LOCK, external_route, source_commit_for
from .local_paths import checked_path
from .monitor_tools import new_directory
from .meta_write_mapping import _write_new
from .operator_review import load_input, pattern
from .partner_brief import review
from .validation import fail, object_only

FIELDS = ('method', 'source_commit', 'method_sha256', 'source_input_sha256',
          'workspace_sha256', 'account_id', 'deliverable', 'brief', 'source_materials')


def _check(value, identity, account):
    object_only(value, (*FIELDS, "application"), FIELDS)
    role = METHOD_ROLES.get(value['method'])
    if role is None or value['source_commit'] != source_commit_for(value['method']):
        fail('METHOD_HANDOFF_SCOPE')
    for key in ('method_sha256', 'source_input_sha256'):
        pattern(value[key], r'[a-f0-9]{64}')
    method = RESEARCH_LOCK['methods'].get(value['method'])
    if method is not None and value['method_sha256'] != method[3]['SKILL.md']:
        fail('METHOD_HANDOFF_SCOPE')
    if (value['workspace_sha256'], value['account_id']) != (identity, account):
        fail('METHOD_HANDOFF_WORKSPACE_MISMATCH')
    text = value['deliverable']
    if type(text) is not str or not 1 <= len(text.strip()) <= 15_000:
        fail('METHOD_HANDOFF_DELIVERABLE_REQUIRED')
    if SECRET_PATTERN.search(text):
        fail('METHOD_HANDOFF_SENSITIVE_CONTENT')
    materials = value['source_materials']
    if type(materials) is not list or not 1 <= len(materials) <= 12:
        fail('METHOD_HANDOFF_SOURCE_MATERIAL_REQUIRED')
    rows = []
    for material in materials:
        keys = ('path', 'sha256')
        object_only(material, keys, keys)
        fingerprint = pattern(material['sha256'], r'[a-f0-9]{64}')
        if type(material['path']) is not str:
            fail('METHOD_HANDOFF_SOURCE_PATH')
        observed = _source_identity(material['path'])
        if observed['sha256'] != fingerprint:
            fail('METHOD_HANDOFF_SOURCE_CHANGED')
        rows.append(fingerprint)
    if digest(rows) != value['source_input_sha256']:
        fail('METHOD_HANDOFF_SOURCE_HASH_MISMATCH')
    checked = review(value['brief'])
    if checked['role'] != role:
        fail('METHOD_HANDOFF_ROLE_MISMATCH')
    if 'application' in value:
        application = value['application']
        fields = ('intent', 'plan_sha256', 'consumer', 'consumer_input_sha256', 'method_documents',
                  'input_requirements', 'assumptions', 'unknowns')
        object_only(application, fields, fields)
        for key in ('plan_sha256', 'consumer_input_sha256'):
            pattern(application[key], r'[a-f0-9]{64}')
        checked['application'] = application
    return dict(checked, method=value['method'], source_commit=value['source_commit'],
                method_sha256=value['method_sha256'], material_hashes=rows,
                full_deliverable_sha256=sha256(text.encode('utf-8')).hexdigest())


def _source_identity(filename):
    path = checked_path(filename, link_error='METHOD_HANDOFF_SOURCE_PATH', volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
    if not path.is_file() or path.stat().st_size > 8_000_000:
        fail('METHOD_HANDOFF_SOURCE_FILE_REQUIRED')
    with path.open('rb') as source:
        content = source.read(8_000_001)
    if len(content) > 8_000_000:
        fail('METHOD_HANDOFF_SOURCE_FILE_REQUIRED')
    return dict(path=str(path), sha256=sha256(content).hexdigest())


def record(directory, account, skill, source_root, source_input, output, source_materials):
    resolved = external_route(skill, source_root, workspace=directory)
    ready = ('SOURCE_METHOD_VERIFIED','BUNDLED_TEXT_METHOD_HASH_VERIFIED',
             'BUNDLED_PRINCIPLES_ONLY_ENGINE_UNAVAILABLE')
    if resolved['status'] not in ready or skill not in METHOD_ROLES:
        fail('METHOD_SOURCE_DEPENDENCIES_REQUIRED')
    result = load_input(source_input)
    fields = ('deliverable', 'brief')
    object_only(result, (*fields, 'source_input_sha256'), fields)
    if not 1 <= len(source_materials) <= 12:
        fail('METHOD_HANDOFF_SOURCE_MATERIAL_REQUIRED')
    materials = [_source_identity(path) for path in source_materials]
    profile = workspace.read(directory, existing_only=True)
    scope = workspace.draft_review_scope(profile, account, host_draft=True)
    receipt, brief = build_receipt(result, skill, resolved, scope, materials)
    root = new_directory(output)
    file = _write_new(root, 'METHOD-RECEIPT.json', receipt)
    return dict(status='HOST_METHOD_RESULT_ACCEPTED', receipt=file, next_entry='media-buyer prepare',
                partner_summary=brief, evidence_layer='HOST_PROVIDED_OUTPUT_AND_VERIFIED_METHOD_FILES',
                network_used=False, actions_authorized=False)


def build_receipt(result, skill, resolved, scope, materials):
    source_digest = digest([item['sha256'] for item in materials])
    if result.get('source_input_sha256', source_digest) != source_digest:
        fail('METHOD_HANDOFF_SOURCE_HASH_MISMATCH')
    value = dict(result, method=skill, source_commit=resolved['source_commit'],
                 method_sha256=resolved['method_sha256'], workspace_sha256=scope['workspace_identity'],
                 account_id=scope['account_id'], source_input_sha256=source_digest, source_materials=materials)
    brief = _check(value, scope['workspace_identity'], scope['account_id'])
    return dict(schema='workspace-method-receipt-v1', payload=value, payload_sha256=digest(value)), brief


def load_receipt(path, identity, account):
    value = load_input(path)
    fields = ('schema', 'payload', 'payload_sha256')
    object_only(value, fields, fields)
    if value['schema'] != 'workspace-method-receipt-v1' or digest(value['payload']) != value['payload_sha256']:
        fail('METHOD_HANDOFF_RECEIPT_MISMATCH')
    return _check(value['payload'], identity, account)


def add_command(sub):
    command = sub.add_parser('workspace-method-record', help='保存完整方法输出并将具名经营摘要交下一步')
    for name in ('workspace', 'input', 'output'):
        command.add_argument('--' + name, type=Path, required=True)
    command.add_argument('--source-root',type=Path,help='可选旧路径。项目内已安装方法及随包核心无需指定。')
    command.add_argument('--skill', choices=sorted(METHOD_ROLES), required=True)
    command.add_argument('--source-material', type=Path, action='append', required=True)
    command.add_argument('--account-id')
