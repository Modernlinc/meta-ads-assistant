"""Resolve Meta task intents to verified local method text and a business handoff."""

from hashlib import sha256
from pathlib import Path
import re

from . import batch_knowledge, client_workspace, method_library
from .business_profile import SECRET_PATTERN
from .journal import digest
from .meta_write_mapping import _write_new
from .method_bundle import CORE_METHOD_ROLES, RESEARCH_LOCK, external_route
from .monitor_tools import new_directory
from .operator_review import load_input
from .validation import InputError, fail, object_only, strict_json

_ROOT = Path(__file__).resolve().parents[2]
_CATALOG = _ROOT / 'references/method-catalog.json'
_READY = ('SOURCE_METHOD_VERIFIED', 'BUNDLED_TEXT_METHOD_HASH_VERIFIED',
          'BUNDLED_PRINCIPLES_ONLY_ENGINE_UNAVAILABLE')
_REINSTALL = ('SOURCE_DIRECTORY_REQUIRED', 'METHOD_INSTALL_UPDATE_REQUIRED',
              'SOURCE_VERSION_MISMATCH', 'SOURCE_DEPENDENCY_MISSING')


def routes():
    catalog = strict_json(_CATALOG.read_text(encoding='utf-8'))
    if catalog['schema'] != 'meta-method-routing-v1' or catalog['platform'] != 'META':
        fail('METHOD_CATALOG_SCHEMA')
    return catalog['routes']


def catalog():
    from .method_bundle import COMMIT

    table = routes()
    rows = []
    for name in (*CORE_METHOD_ROLES, *RESEARCH_LOCK['methods']):
        linked = [key for key, row in table.items() if name in (*row['core'], *row['optional'])]
        if name in CORE_METHOD_ROLES:
            source = dict(repo='Modernlinc/ecommerce-skills', pinned_commit=COMMIT,
                          entry=f'methods/{name}/entry.md', license=f'methods/{name}/LICENSE.txt',
                          notice=f'methods/{name}/NOTICE.md', method_role=CORE_METHOD_ROLES[name])
            scope = 'BUNDLED'
            status = 'MANIFEST_HASH_REQUIRED'
        else:
            repository, commit, files, notice = method_library._spec(name)
            group, entry, role, _ = RESEARCH_LOCK['methods'][name]
            source = dict(repo=repository, pinned_commit=commit, entry=entry, license='MIT', group=group,
                          license_sha256=files['LICENSE'], notice_sha256=sha256(notice.encode()).hexdigest(), method_role=role)
            scope = 'OPTIONAL'
            status = 'LOCK_VALIDATED_INSTALL_CHECKS_BYTES'
        rows.append(dict(method_id=name, display_name=name.removeprefix('r21-').removeprefix('r22-').removeprefix('r25-')
                         .replace('-', ' '), capability_tags=linked, bundled_or_optional=scope,
                         source=source, validation_status=status,
                         prerequisites=['verified workspace method files'],
                         applicability=[dict(intent=key, **{field: table[key][field] for field in
                            ('intent_triggers', 'required_inputs', 'outputs', 'downstream_consumers', 'known_limitations')})
                                        for key in linked]))
    return dict(platform='META', routes=table, methods=rows, knowledge_batch01={
        key:batch_knowledge.candidates(key) for key in table})


def infer_intents(objective, table):
    if type(objective) is not str or not 1 <= len(objective.strip()) <= 600 or SECRET_PATTERN.search(objective):
        fail('METHOD_OBJECTIVE_INVALID')
    text = objective.casefold()
    if re.search(r'google ads|tiktok ads|千川|谷歌广告|抖音投放|直播运营', text):
        return []
    matches = [key for key, row in table.items() if any(term in text for term in row['intent_triggers'])]
    # More specific goals remain actionable; mentioning their input alone
    # does not also start a separate creative/brand workflow.
    if 'creative' in matches and {'experiment','competitor'} & set(matches):
        matches.remove('creative')
    return matches


def _scope(directory, account):
    profile = client_workspace.read(directory, existing_only=True)
    selected = account if account is not None else profile['defaults']['ACCOUNT']
    if selected is not None:
        selected = client_workspace.business_account(profile, selected)
    business = profile.get('business_profiles', {}).get(selected)
    return dict(workspace_sha256=client_workspace.workspace_identity(profile), account_id=selected,
                selected_default=account is None, business_profile_sha256=digest(business),
                connection_status=(profile.get('checks', {}).get('ACCOUNT:'+selected, {}).get('status','UNCHECKED')
                                   if selected is not None else 'NOT_SELECTED'),
                business_profile_available=business is not None)


def _resolve(workspace, name, offline):
    try:
        found = external_route(name, workspace=workspace)
        network = False
        if name not in CORE_METHOD_ROLES and found['status'] in _REINSTALL:
            if offline:
                return dict(method_id=name, status='METHOD_OPTIONAL_OFFLINE', network_used=False)
            recovered = method_library.install(workspace, name)
            network = recovered['network_used']
            found = external_route(name, workspace=workspace)
        if found['status'] not in _READY:
            return dict(method_id=name, status=found['status'], network_used=network)
        files = [file for file in found['read_files'] if file.endswith('.md') and file != 'NOTICE.md']
        return dict(method_id=name, status=found['status'], source_directory=found['source_directory'],
                    source_commit=found['source_commit'], method_sha256=found['method_sha256'],
                    read_files=files, entry=Path(found.get('entry', 'SKILL.md')).name,
                    notice=found.get('notice', 'NOTICE.md'), network_used=network)
    except (InputError, OSError, UnicodeError) as error:
        return dict(method_id=name, status=str(error) if isinstance(error, InputError) else 'METHOD_SOURCE_UNREADABLE',
                    network_used=None)


def plan(workspace, account, intents, objective, output, *, offline=False):
    table = routes()
    selected = infer_intents(objective, table) if objective is not None else list(dict.fromkeys(intents))
    if not selected or len(selected) > 4 or set(selected) - table.keys():
        return dict(status='HOST_INTENT_REQUIRED', available_intents=list(table), actions_authorized=False)
    scope = _scope(workspace, account)
    names = dict.fromkeys(name for key in selected for category in ('core', 'optional') for name in table[key][category])
    methods = {name: _resolve(workspace, name, offline) for name in names}
    tasks = [{
        'intent': key, **table[key], 'knowledge_candidates': batch_knowledge.candidates(key),
        'status': ('METHOD_CORE_UNAVAILABLE' if any(methods[name]['status'] not in _READY for name in table[key]['core'])
                   else 'METHODS_READY'),
    } for key in selected]
    value = dict(schema='meta-method-plan-v1', scope=scope, tasks=tasks, methods=methods,
                 host_application='PENDING', actions_authorized=False)
    envelope = dict(payload=value, payload_sha256=digest(value))
    if output is not None:
        directory = new_directory(output)
        _write_new(directory, 'METHOD-PLAN.json', envelope)
        envelope['plan_file'] = str(directory / 'METHOD-PLAN.json')
    return envelope


def read_plan(path, directory, intent):
    envelope = load_input(path)
    object_only(envelope, ('payload','payload_sha256'), ('payload','payload_sha256'))
    value = envelope['payload']
    if (type(value) is not dict or value.get('schema') != 'meta-method-plan-v1'
            or digest(value) != envelope['payload_sha256']):
        fail('METHOD_PLAN_MISMATCH')
    prior = value.get('scope')
    keys = ('workspace_sha256','account_id','selected_default','business_profile_sha256','connection_status',
            'business_profile_available')
    object_only(prior, keys, keys)
    if type(prior['selected_default']) is not bool or type(value.get('tasks')) is not list or type(value.get('methods')) is not dict:
        fail('METHOD_PLAN_MISMATCH')
    actual = _scope(directory, None if prior['selected_default'] else prior['account_id'])
    if actual != prior:
        fail('METHOD_PLAN_WORKSPACE_CHANGED')
    task = next((row for row in value['tasks'] if type(row) is dict and row.get('intent') == intent), None)
    expected = routes()[intent]
    if (task is None or task['status'] != 'METHODS_READY'
            or task.get('knowledge_candidates') != batch_knowledge.candidates(intent)
            or any(task.get(key) != wanted for key, wanted in expected.items())):
        fail('METHOD_PLAN_INTENT_UNAVAILABLE')
    ready = {}
    available = value['methods']
    for name in (*expected['core'], *expected['optional']):
        saved = available.get(name, {})
        if type(saved) is not dict or saved.get('status') not in _READY:
            if name in expected['core']:
                fail('METHOD_PLAN_CORE_UNAVAILABLE')
            continue
        latest = _resolve(directory, name, offline=True)
        for key in ('status', 'source_commit', 'method_sha256', 'read_files'):
            if latest.get(key) != saved.get(key):
                fail('METHOD_PLAN_METHOD_CHANGED')
        ready[name] = latest
    return actual, expected, ready, envelope['payload_sha256']


def add_commands(sub):
    sub.add_parser('method-catalog', help='助手按Meta业务意图读取方法目录、适用条件和去向')
    planner = sub.add_parser('method-plan', help='助手从客户目标加载随包或固定来源方法，无须客户提供方法名')
    planner.add_argument('--workspace', required=True, type=Path)
    planner.add_argument('--account-id')
    planner.add_argument('--offline', action='store_true', help='只使用已验证的本地方法')
    planner.add_argument('--output', type=Path, help='保存本次方法选择及来源，供方法成果续接')
    group = planner.add_mutually_exclusive_group(required=True)
    group.add_argument('--intent', action='append', choices=routes(), help='由助手按上下文选择业务意图，可组合')
    group.add_argument('--objective', help='可识别的中文或英文业务目标，勿包含凭据或客户私密信息')
    apply = sub.add_parser('method-apply', help='把助手已应用的方法及成果交由本机业务模块实际处理和留痕')
    for key in ('workspace','plan','input','output'):
        apply.add_argument('--'+key, type=Path, required=True)
    apply.add_argument('--intent', choices=routes(), required=True)
    apply.add_argument('--source-material', type=Path, action='append', required=True)
