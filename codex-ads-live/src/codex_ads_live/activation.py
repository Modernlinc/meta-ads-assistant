"""Review activation and budget changes against a closed, seeded simulator."""
from copy import deepcopy
import hashlib

from .journal import canonical, digest
from .materials import check_media_files, material_findings, validate_material
from .meta_write_mapping import validate_draft, validate_targeting, utcnow
from .validation import aware_time, fail, object_only
from .write_snapshots import validate_allowlist


def is_activation(draft):
    return draft['operation'] == 'SET_STATUS' and draft['parameters']['status'] == 'ACTIVE'


def is_budget_change(draft):
    return draft['operation'] == 'SET_ADSET_DAILY_BUDGET'


def inventory(data, draft, allowed):
    """Enumerate before filtering by permission so an omitted child cannot vanish."""
    validate_allowlist(allowed)
    account = draft['account_id']
    level = draft['target_level']
    target = draft['target_id']
    budget = is_budget_change(draft)
    if not (is_activation(draft) or budget and level == 'ADSET') or allowed['account_id'] != account:
        fail('ACTIVATION_SCOPE')
    nodes = data['nodes']

    def named(kind, identifier):
        node = nodes.get(kind + ':' + identifier)
        if identifier not in allowed[kind] or node is None:
            fail('ACTIVATION_UNLISTED_OR_MISSING_OBJECT')
        row = node['data']
        if kind != 'PAGE' and row['account_id'] != account:
            fail('ACTIVATION_ACCOUNT_MISMATCH')
        return row

    root = named(level, target)
    campaign_id = root['id'] if level == 'CAMPAIGN' else root['campaign_id']
    campaign = named('CAMPAIGN', campaign_id)
    adset_ids = set()
    if level == 'CAMPAIGN':
        for node in nodes.values():
            row = node['data']
            if node['plan']['level'] == 'ADSET' and row['campaign_id'] == target:
                adset_ids.add(row['id'])
    else:
        adset_ids.add(target if level == 'ADSET' else root['adset_id'])

    sets = []
    for identifier in sorted(adset_ids):
        row = named('ADSET', identifier)
        if row['campaign_id'] != campaign_id:
            fail('ACTIVATION_PARENT_MISMATCH')
        sets.append(deepcopy(row))
    ads = []
    creative_hashes = {}
    for key in sorted(nodes):
        node = nodes[key]
        if node['plan']['level'] != 'AD':
            continue
        row = node['data']
        if level == 'AD' and row['id'] != target:
            continue
        if row['adset_id'] not in adset_ids and not (level == 'CAMPAIGN' and row['campaign_id'] == target):
            continue
        named('AD', row['id'])
        if row['campaign_id'] != campaign_id or row['adset_id'] not in adset_ids:
            fail('ACTIVATION_PARENT_MISMATCH')
        creative_id = row['creative']['id']
        page = named('CREATIVE', creative_id)['actor_id']
        named('PAGE', page)
        content = data['materials'].get(creative_id)
        if content is None:
            fail('ACTIVATION_CREATIVE_CONTENT_REQUIRED')
        creative_hashes[creative_id] = dict(page_id=page, sha256=digest(content))
        ads.append(deepcopy(row))
    tree = dict(account_id=account, target_level=level, target_id=target, campaign_id=campaign_id,
                adsets=sets, ads=ads, creatives=creative_hashes, target_before=deepcopy(root), campaign=deepcopy(campaign))
    if budget:
        targeting = nodes['ADSET:' + target]['configuration'].get('targeting')
        if targeting is None:
            fail('BUDGET_TARGETING_REQUIRED')
        validate_targeting(targeting)
        if nodes['CAMPAIGN:' + campaign_id]['configuration'].get('is_adset_budget_sharing_enabled') is not False:
            fail('BUDGET_CAMPAIGN_SHARING_UNKNOWN_OR_ENABLED')
        tree.update(targeting=deepcopy(targeting), campaign_budget_sharing=False)
    return tree


CURRENT = 'synthetic-activation-coverage-v2'
LEGACY = 'synthetic-activation-coverage-v1'
BUDGET = 'synthetic-budget-coverage-v1'


def validate_coverage(draft, coverage, clock, *, historical=False):
    keys = ('schema', 'inventory', 'materials')
    object_only(coverage, keys, keys)
    budget = is_budget_change(draft)
    schemas = (BUDGET,) if budget else (LEGACY, CURRENT)
    if coverage['schema'] not in schemas or len(canonical(coverage).encode()) > 400000:
        fail('ACTIVATION_COVERAGE_FORMAT')
    tree = coverage['inventory']
    keys = ('account_id', 'target_level', 'target_id', 'campaign_id', 'adsets', 'ads', 'creatives', 'target_before')
    if coverage['schema'] != LEGACY:
        keys += ('campaign',)
    elif not historical:
        fail('ACTIVATION_LEGACY_REPROPOSAL_REQUIRED')
    if budget:
        keys += ('targeting', 'campaign_budget_sharing')
    object_only(tree, keys, keys)
    markets = set()
    if budget:
        validate_targeting(tree['targeting'])
        if tree['campaign_budget_sharing'] is not False:
            fail('BUDGET_CAMPAIGN_SHARING_UNKNOWN_OR_ENABLED')
        markets = set(tree['targeting']['geo_locations']['countries'])
    if any(tree[key] != draft[key] for key in ('account_id', 'target_level', 'target_id')):
        fail('ACTIVATION_SCOPE')
    materials = coverage['materials']
    if type(materials) is not dict or type(tree['creatives']) is not dict or set(materials) != set(tree['creatives']):
        fail('ACTIVATION_COVERAGE_INCOMPLETE')
    total_bytes = 0
    for identifier, item in materials.items():
        validate_material(item)
        expected = dict(page_id=item['page_id'], sha256=digest(item['creative']))
        if (item['source_mode'] != 'SYNTHETIC' or item['creative_id'] != identifier
                or item['account_id'] != draft['account_id'] or tree['creatives'][identifier] != expected):
            fail('ACTIVATION_MATERIAL_SCOPE_OR_CONTENT')
        at = clock()
        if material_findings(item, clock=lambda: at)[1]:
            fail('ACTIVATION_MATERIAL_INCOMPLETE')
        if not markets <= set(item['intended_use']['countries']):
            fail('BUDGET_MATERIAL_MARKET_MISMATCH')
        if coverage['schema'] != LEGACY and at < aware_time(item['intended_use']['starts_at']):
            fail('BUDGET_USE_NOT_STARTED' if budget else 'ACTIVATION_USE_NOT_STARTED')
        total_bytes += sum(entry['bytes'] for entry in item['media'])
    if total_bytes > 250_000_000:
        fail('ACTIVATION_MEDIA_SIZE_LIMIT')


def match_budget_baseline(coverage, bundle):
    tree = coverage['inventory']
    rows = {entry['plan']['level']: entry['data'] for entry in bundle['entries']}
    if tree['target_before'] != rows.get('ADSET') or tree['campaign'] != rows.get('CAMPAIGN'):
        fail('BUDGET_BASELINE_INVENTORY_MISMATCH')


def prepare_activation(draft, allowed, service, materials, asset_root, *, clock=utcnow):
    return prepare_impact(draft, allowed, service, materials, asset_root, clock=clock)


def prepare_impact(draft, allowed, service, materials, asset_root, *, budget=False, clock=utcnow):
    from .mapped_service import SyntheticMappedService
    draft = deepcopy(draft)
    validate_draft(draft)
    if not (is_budget_change(draft) if budget else is_activation(draft)) or type(service) is not SyntheticMappedService:
        fail('ACTIVATION_SYNTHETIC_SERVICE_REQUIRED')
    capture = service.budget_inventory if budget else service.activation_inventory
    coverage = dict(schema=BUDGET if budget else CURRENT,
                    inventory=capture(draft, allowed), materials=deepcopy(materials))
    if not budget and coverage['inventory']['target_before']['status'] != 'PAUSED':
        fail('ACTIVATION_TARGET_MUST_BE_PAUSED')
    validate_coverage(draft, coverage, clock)
    for item in coverage['materials'].values():
        check_media_files(item, asset_root)
    if capture(draft, allowed) != coverage['inventory']:
        fail('ACTIVATION_INVENTORY_CHANGED')
    validate_coverage(draft, coverage, clock)
    bound = digest(coverage)
    if draft['context']['material_sha256'] not in (None, bound):
        fail('ACTIVATION_REVIEW_CHANGED')
    draft['context']['material_sha256'] = bound
    return draft, coverage


def export_activation(draft_path, allowlist_path, service_directory, material_paths, asset_root, output, *, budget=False):
    """Prepare a review for an existing closed simulator, using named local files."""
    from .local_paths import checked_path
    from .mapped_service import SyntheticMappedService
    from .materials import export_material, load_material
    from .meta_write_mapping import _write_new, load_draft
    from .monitor_tools import new_directory
    from .validation import strict_json

    def checked(path):
        return checked_path(path, link_error='ACTIVATION_PATH_LINK', volume_error='ACTIVATION_VOLUME_NOT_MOUNTED')

    service_directory = checked(service_directory)
    if not checked(service_directory / 'operator-state.json').is_file():
        fail('ACTIVATION_SERVICE_NOT_FOUND')
    allowlist_path = checked(allowlist_path)
    if not allowlist_path.is_file() or allowlist_path.stat().st_size > 16000:
        fail('ACTIVATION_ALLOWLIST_FILE')
    with allowlist_path.open('rb') as handle:
        raw = handle.read(16001)
    if len(raw) > 16000:
        fail('ACTIVATION_ALLOWLIST_FILE')
    allowed = validate_allowlist(strict_json(raw.decode('utf-8')))
    if len(material_paths) > len(allowed['CREATIVE']):
        fail('ACTIVATION_MATERIAL_COUNT')
    materials = {}
    for path in material_paths:
        material = load_material(path)
        identifier = material['creative_id']
        if identifier in materials or identifier not in allowed['CREATIVE']:
            fail('ACTIVATION_DUPLICATE_OR_UNLISTED_CREATIVE')
        materials[identifier] = material
    service = SyntheticMappedService(service_directory)
    draft, coverage = prepare_impact(load_draft(draft_path), allowed, service,
                                     materials, asset_root, budget=budget)
    from .write_snapshots import collect_snapshots, review_snapshots
    review = None
    if budget:
        baseline = collect_snapshots(draft, allowed, service.snapshot_factory)
        review = review_snapshots(draft, baseline)
        match_budget_baseline(coverage, strict_json(baseline.payload))
        if review['missing_fields'] or 'NO_CHANGE_DO_NOT_SEND' in review['blockers']:
            fail('BUDGET_DRAFT_INCOMPLETE_OR_NO_CHANGE')
    root = new_directory(output)
    files = [_write_new(root, 'draft.json', draft), _write_new(root, 'coverage.json', coverage)]
    tree = coverage['inventory']
    if budget:
        files.append(_write_new(root, 'review.json', review))
    for identifier, item in sorted(materials.items()):
        folder = root / 'creatives' / identifier
        report = export_material(item, asset_root, folder)
        if report['findings']:
            fail('ACTIVATION_MATERIAL_INCOMPLETE')
        receipt = folder / 'RECEIPT.json'
        files.append(dict(path=str(receipt.relative_to(root).as_posix()), bytes=receipt.stat().st_size,
                          sha256=hashlib.sha256(receipt.read_bytes()).hexdigest()))
    if budget:
        before = review['draft']['before']['daily_budget_minor']
        after = draft['parameters']['daily_budget']
        ctx = review['draft']['context']
        targeting = tree['targeting']
        title = '# 模拟日预算调整影响清单'
        action = (f"日预算从 {before} 调整为 {after}（{ctx['currency']} 最小单位），"
                  f"账户时区 {ctx['timezone']}。广告组配置状态为 {tree['target_before']['status']}，"
                  f"当前观察到的有效状态为 {tree['target_before']['effective_status']}。")
        delivery = ("市场 " + '、'.join(targeting['geo_locations']['countries'])
                    + f"，年龄 {targeting['age_min']} 至 {targeting['age_max']}。模拟系列的预算共享已关闭。实际花费仍需通过投放数据核对。")
    else:
        title = '# 模拟启用影响清单'
        action = (f"目标配置状态从 {tree['target_before']['status']} 改为 ACTIVE，观察到的有效状态为 "
                  f"{tree['target_before']['effective_status']}。")
        delivery = ''
    lines = [title, '',
             f"账户 {tree['account_id']}，目标 {tree['target_level']} {tree['target_id']}。",
             action, delivery,
             f"此次核对覆盖 {len(tree['adsets'])} 个广告组、{len(tree['ads'])} 条广告、{len(materials)} 份创意。", '',
             f"所属系列 {tree['campaign_id']}，当前配置状态 {tree['campaign']['status']}，"
             f"有效状态 {tree['campaign']['effective_status']}。", '',
             '广告组清单（含空组）', '', '| 广告组 | 配置状态 | 有效状态 | 涵盖广告数 |', '|---|---|---|---|---|']
    for group in tree['adsets']:
        count = sum(ad['adset_id'] == group['id'] for ad in tree['ads'])
        lines.append(f"| {group['id']} | {group['status']} | {group['effective_status']} | {count} |")
    lines += ['', '广告清单', '', '| 广告组 | 广告 | 配置状态 | 观察到的有效状态 | 创意 | 主页 |',
             '|---|---|---|---|---|---|']
    for ad in tree['ads']:
        identifier = ad['creative']['id']
        lines.append(f"| {ad['adset_id']} | {ad['id']} | {ad['status']} | "
                     f"{ad['effective_status']} | [{identifier}](creatives/{identifier}/MATERIAL_REVIEW.md) | "
                     f"{materials[identifier]['page_id']} |")
    if not tree['ads']:
        lines.append(''); lines.append('在该模拟服务的完整目录内，此目标当前没有子广告。')
    lines.extend(['', '完整的分组、逐条素材和权利声明见 [coverage.json](coverage.json)。',
                  '实际投放、素材权利与真人审批仍需各自的验证。'])
    body = ('\n'.join(lines) + '\n').encode('utf-8')
    with (root / 'REVIEW.md').open('xb') as handle:
        handle.write(body)
    files.append(dict(path='REVIEW.md', bytes=len(body), sha256=hashlib.sha256(body).hexdigest()))
    status = 'SYNTHETIC_BUDGET_REVIEW_PREPARED' if budget else 'SYNTHETIC_ACTIVATION_REVIEW_PREPARED'
    result = dict(status=status, material_sha256=digest(coverage),
                  adsets=len(tree['adsets']), ads=len(tree['ads']), creatives=len(materials),
                  dispatchable=False, network_used=False)
    capture = service.budget_inventory if budget else service.activation_inventory
    if capture(draft, allowed) != tree:
        fail('ACTIVATION_INVENTORY_CHANGED')
    validate_coverage(draft, coverage, utcnow)
    _write_new(root, 'RECEIPT.json', dict(**result, files=files))
    return result
