"""Compile an implementable site measurement plan and its local audit input."""
from pathlib import Path
import json
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .client_workspace import business_account, read, workspace_identity
from .journal import digest
from .meta_write_mapping import _write_new
from .monitor_tools import new_directory
from .operator_review import evidence_text, pattern, tracking_audit, tracking_path
from .validation import fail

STACKS = ('shopify-native', 'gtm', 'custom-web')
STANDARD = frozenset(('Purchase', 'Lead', 'InitiateCheckout', 'AddToCart', 'ViewContent', 'CompleteRegistration'))
DEDUPE_DOC = 'https://developers.facebook.com/docs/marketing-api/conversions-api/deduplicate-pixel-and-server-events/'
SHOPIFY_DOC = 'https://help.shopify.com/en/manual/promoting-marketing/analyze-marketing/meta-pixel'
GTM_DOC = 'https://developers.google.com/tag-platform/tag-manager/datalayer'
STEPS = {
    'shopify-native': (
        '由店铺管理员在 Facebook & Instagram by Meta 的数据共享界面选择对应数据集和适用同意设置。'
        '测试前清点主题、店铺应用及标签容器中的旧像素，先保留旧配置记录。',
        '回到应用数据共享设置，恢复上次批准的数据集与共享级别，再在事件管理器核源。'
    ),
    'gtm': (
        '创建工作区版本并留存旧容器。浏览器数据层在实际完成条件发一次事件。配置事件名、共用 ID 与同意状态。'
        '使用 Tag Assistant 预览和服务端标签测试核触发，再由管理员按批准发布。',
        '回滚已审核的容器版本，恢复原触发器和去重配置。不要同时启用旧、新两套规则。'
    ),
    'custom-web': (
        '在可回滚分支接前端和订单或线索成功回调。服务端为同一业务事件生成稳定不含个人信息的 ID。'
        '同一名称和 ID 传给浏览器与服务端。用测试环境验证再提交部署。',
        '关闭新增入口并部署原版本，保留对照期间的诊断回执。'
    ),
}


def _configuration_files(root, stack, name, dataset):
    if stack == 'shopify-native':
        return []
    event_function = 'track' if name in STANDARD else 'trackCustom'
    files = []
    data = dict(event='commerce_conversion_ready', conversion=dict(event_name=name, event_id='APP_GENERATED_ID',
                value=12.34 if name == 'Purchase' else None, currency='USD' if name == 'Purchase' else None))
    if stack == 'gtm':
        files.append(_write_new(root, 'gtm-draft.json', dict(
            data_layer_example=data, trigger_type='Custom Event', trigger_event_name=data['event'],
            data_layer_variables=['conversion.event_name', 'conversion.event_id', 'conversion.value',
                                  'conversion.currency'],
            browser_tag=dict(dataset_id=dataset, function=event_function, event='conversion.event_name',
                             event_options={'eventID': 'conversion.event_id'}),
            server_mapping=dict(event_name='conversion.event_name', event_id='conversion.event_id',
                                action_source='website'),
            installation_state='CONFIGURATION_DRAFT',
        )))
    else:
        lines = [
            '# 浏览器和服务端事件配置示例', '',
            '在实际转换成功并满足同意要求时调用。以下示例依赖已有 Meta Pixel；用应用生成的稳定 ID 替换占位值。',
            '', '```javascript', 'const eventId = validatedConversionId;',
            'const eventParams = ' + ('{ value: verifiedOrderValue, currency: verifiedCurrency };'
                                      if name == 'Purchase' else '{};'),
            f'fbq({json.dumps(event_function)}, {json.dumps(name)}, eventParams, {{ eventID: eventId }});',
            '```', '',
            '服务端通过已部署并具合适授权的 Conversions API 适配器提交以下字段，应用端负责实际变量与同意状态。',
            '', '```json',
            json.dumps(dict(event_name=name, event_id='APP_GENERATED_ID', event_time='UNIX_EVENT_SECONDS',
                            action_source='website', event_source_url='APPROVED_EVENT_URL',
                            custom_data={'value': 'VERIFIED_VALUE', 'currency': 'VERIFIED_CURRENCY'}
                            if name == 'Purchase' else {}), indent=2), '```', '',
            '补齐当前平台要求的和已获同意的匹配字段，再测试事件。示例中的字符串占位值需转换成平台要求的类型。'
        ]
        name = 'custom-web-draft.md'
        with (root / name).open('x', encoding='utf-8') as stream:
            stream.write('\n'.join(lines) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        files.append({'path': name})
    return files


def prepare(directory, account, output, *, destination, dataset, event, trigger, stack, existing, campaign):
    profile = read(directory, existing_only=True)
    account = business_account(profile, account) if account is not None or profile['defaults']['ACCOUNT'] else None
    goal = evidence_text(trigger)
    name = pattern(event, r'[A-Za-z][A-Za-z0-9_]{0,79}')
    if dataset is not None:
        dataset = pattern(dataset, r'[1-9][0-9]{0,39}')
    keys, duplicates = tracking_path(destination)
    parts = urlsplit(destination)
    query = parse_qsl(parts.query, keep_blank_values=True)
    defaults = dict(utm_source='meta', utm_medium='paid_social', utm_campaign=pattern(campaign, r'[A-Za-z0-9_-]{1,80}'))
    query.extend((key, value) for key, value in defaults.items() if key not in keys)
    tagged_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    native = stack == 'shopify-native'
    steps, rollback = STEPS[stack]
    config = dict(dataset_id=dataset, destination=tagged_url,
                  observed_utm_keys=keys, duplicate_utm_keys=duplicates,
                  primary_event=dict(name=name, kind='STANDARD' if name in STANDARD else 'CUSTOM', trigger=goal),
                  browser=dict(field='eventID', event_name=name),
                  server=dict(field='event_id', event_name=name,
                              source='website', stable_id_from='SAME_CONFIRMED_BUSINESS_EVENT'),
                  allowed_data='依据已批准的同意和数据策略，只传必要数据')
    if name == 'Purchase':
        config['parameters'] = ['value', 'currency']
        config['value_basis'] = '以主业务已确认的收入口径填写，在同一购买事件中使用同一币种。'
    if native and name not in STANDARD:
        config['integration_review'] = '先核原生渠道能否发这个自定义事件。需单独站点路径时避免重复发原生事件。'
    if existing:
        steps = '先在测试面板核当前数据集、发出渠道与事件名称，保留未删改的设置快照。' + steps
    docs = [DEDUPE_DOC] + ([SHOPIFY_DOC] if native else [GTM_DOC] if stack == 'gtm' else [])
    audit_doc = dict(schema='operator-tracking-v1', dataset_id=dataset, destination=tagged_url, events=[])
    blockers = [reason for condition, reason in ((dataset is None, 'SELECT_DATASET'),
                                                  (bool(duplicates), 'RESOLVE_DUPLICATE_UTM')) if condition]
    report = dict(status='TRACKING_CONFIGURATION_PREPARED', workspace_sha256=workspace_identity(profile),
                  account_id=account, stack=stack, scenario='REPAIR' if existing else 'INITIAL_SETUP',
                  configuration=config, deployment_steps=steps, rollback_steps=rollback,
                  verification_steps=[
                      '记录同意允许时的测试完成事件。核对数据集、事件日期、事件名、业务触发和字段。',
                      '双发时核同一事件名与 ID；检查缺 ID、同渠道重复及不同名串用。单渠道保持独立。',
                      '把已观察的测试事件交 tracking-audit。另记录真实观察，保留配置、事件观察和效果的状态。',
                  ],
                  blockers=blockers, source_urls=docs, tag_deployed=False, test_events_observed=False,
                  production_events_observed=False, actions_authorized=False)
    root = new_directory(output)
    report['configuration_files'] = _configuration_files(root, stack, name, dataset)
    if dataset is not None and not duplicates:
        report['audit_input'] = _write_new(root, 'tracking-input.json', audit_doc)
        report['local_audit'] = tracking_audit(audit_doc)
        report['next_entry'] = 'tracking-audit'
    _write_new(root, 'TRACKING-CONFIGURATION.json', report)
    return report


def consume_configuration(path, scope, document, goal):
    from .operator_review import load_input

    draft = load_input(path)
    expected = ('TRACKING_CONFIGURATION_PREPARED', scope['workspace_identity'], scope['account_id'])
    if type(draft) is not dict or tuple(draft.get(field) for field in ('status', 'workspace_sha256', 'account_id')) != expected:
        fail('BUYER_TRACKING_CONFIGURATION_SCOPE')
    config = draft.get('configuration')
    if type(config) is not dict or (config.get('dataset_id'), config.get('destination')) != (document['dataset_id'], document['destination']):
        fail('BUYER_TRACKING_CONFIGURATION_SCOPE')
    primary = config.get('primary_event')
    if type(primary) is not dict:
        fail('BUYER_TRACKING_CONFIGURATION_SCOPE')
    name = pattern(primary.get('name'), r'[A-Za-z][A-Za-z0-9_]{0,79}')
    kind = 'STANDARD' if name in STANDARD else 'CUSTOM'
    if primary.get('kind') != kind or draft.get('actions_authorized') is not False:
        fail('BUYER_TRACKING_CONFIGURATION_SCOPE')
    counts = {'TEST': 0, 'PRODUCTION': 0}
    for event in document['events']:
        if event['name'] == name:
            counts[event.get('mode', 'PRODUCTION')] += 1
    if kind == 'CUSTOM':
        match = 'REVIEW_CUSTOM_EVENT_SEMANTICS'
    elif name != {'PURCHASE': 'Purchase', 'LEAD': 'Lead'}.get(goal):
        match = 'DECLARED_GOAL_EVENT_MISMATCH'
    else:
        match = 'DECLARED_GOAL_EVENT_ALIGNED'
    return dict(configuration_sha256=digest(draft), goal=goal, event=name, event_kind=kind, goal_match=match,
                evidence_counts=counts, status='OPERATOR_OBSERVATION_ATTACHED' if any(counts.values()) else 'CONFIGURATION_ONLY',
                native_receipt_verified=False, dispatchable=False)


def add_command(sub):
    command = sub.add_parser('tracking-prepare', help='按实际站点生成追踪配置、检查和回退步骤')
    command.add_argument('--workspace', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True)
    command.add_argument('--account-id')
    command.add_argument('--destination', required=True)
    command.add_argument('--dataset')
    command.add_argument('--event', required=True)
    command.add_argument('--trigger', required=True)
    command.add_argument('--stack', choices=STACKS, required=True)
    command.add_argument('--existing', action='store_true')
    command.add_argument('--campaign', required=True)
