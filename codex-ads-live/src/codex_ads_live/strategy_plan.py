"""Compile a selected customer strategy into explicit Meta parent and budget scopes."""
from decimal import Decimal
import re

from .journal import digest
from .meta_write_mapping import PLACEMENT_FIELDS, SUPPORTED_CURRENCIES, validate_targeting
from .validation import aware_time, fail, object_only

GOALS = {
    "PURCHASE": ("OUTCOME_SALES", "OFFSITE_CONVERSIONS", "PURCHASE"),
    "LEAD": ("OUTCOME_LEADS", "OFFSITE_CONVERSIONS", "LEAD"),
}


def _name(value):
    if type(value) is not str or value != value.strip() or not 1 <= len(value) <= 120 or not value.isprintable():
        fail('STRATEGY_PLAN_NAME')
    return value


def _identifier(value):
    if type(value) is not str or not re.fullmatch(r'[1-9][0-9]{0,39}', value):
        fail('STRATEGY_PLAN_OBJECT_ID')
    return value


def _money(value, currency):
    if type(value) is not str or not re.fullmatch(r'[0-9]{1,9}(?:\.[0-9]{1,2})?', value):
        fail('STRATEGY_PLAN_BUDGET_FORMAT')
    scale = Decimal(value).scaleb(0 if currency == 'JPY' else 2)
    if scale != scale.to_integral_value() or not 1 <= scale <= 100_000_000:
        fail('STRATEGY_PLAN_BUDGET_UNITS')
    return int(scale)


def _target(value):
    required = ('geo_locations', 'age_min', 'age_max')
    optional = ('interests', 'custom_audiences', 'excluded_custom_audiences', 'placements')
    object_only(value, (*required, *optional), required)
    out = {key: value[key] for key in required}
    validate_targeting(out)
    for field in ('interests', 'custom_audiences', 'excluded_custom_audiences'):
        ids = value.get(field, [])
        if type(ids) is not list or len(ids) > 10:
            fail('STRATEGY_PLAN_AUDIENCE_FIELDS')
        checked = [_identifier(item) for item in ids]
        if len(checked) != len(set(checked)):
            fail('STRATEGY_PLAN_AUDIENCE_FIELDS')
        if checked:
            if field == 'interests':
                out['flexible_spec'] = [{field: [dict(id=ident) for ident in checked]}]
            else:
                out[field] = [dict(id=ident) for ident in checked]
    if set(value.get('custom_audiences', [])) & set(value.get('excluded_custom_audiences', [])):
        fail('STRATEGY_PLAN_AUDIENCE_OVERLAP')
    placements = value.get('placements', {})
    object_only(placements, tuple(PLACEMENT_FIELDS))
    if placements:
        out['publisher_platforms'] = sorted(placements)
        for platform, positions in placements.items():
            if type(positions) is not list or not positions or any(
                    type(position) is not str or position not in PLACEMENT_FIELDS[platform] for position in positions):
                fail('STRATEGY_PLAN_PLACEMENT_FIELDS')
            out[platform + '_positions'] = sorted(set(positions))
    validate_targeting(out, advanced=True)
    return out


def compile_selection(selection, *, account, currency, goal, dataset, asset_ids, maximum_minor):
    fields = ('mode', 'campaign_name', 'budget_owner', 'budget_schedule', 'campaign_budget',
              'start_time', 'end_time', 'campaign_id', 'groups')
    object_only(selection, (*fields, 'source_campaign_id', 'sequence'), fields)
    mode, owner, schedule = (selection[k] for k in ('mode', 'budget_owner', 'budget_schedule'))
    if mode not in ('NEW', 'APPEND', 'REBUILD') or owner not in ('ABO', 'CBO') or schedule not in ('DAILY', 'LIFETIME'):
        fail('STRATEGY_PLAN_MODE')
    if currency not in SUPPORTED_CURRENCIES or goal not in GOALS or maximum_minor is None:
        fail('STRATEGY_PLAN_POLICY_OR_BUDGET_REQUIRED')
    sequence = selection.get('sequence', 'FULL')
    if sequence not in ('FULL', 'STRUCTURE_FIRST') or (sequence == 'STRUCTURE_FIRST' and mode == 'APPEND'):
        fail('STRATEGY_PLAN_SEQUENCE')
    objective, optimization, event = GOALS[goal]
    _identifier(account)
    _identifier(dataset)
    field = 'daily_budget' if schedule == 'DAILY' else 'lifetime_budget'
    start, end = selection['start_time'], selection['end_time']
    if (start is None) != (end is None) or (schedule == 'LIFETIME' and end is None):
        fail('STRATEGY_PLAN_WINDOW_REQUIRED')
    window_fields = {}
    if end:
        first, last = aware_time(start), aware_time(end)
        if first >= last:
            fail('STRATEGY_PLAN_WINDOW_ORDER')
        window_fields = dict(start_time=first.isoformat(), end_time=last.isoformat())
    source_campaign = selection.get('source_campaign_id')
    if (source_campaign is not None) != (mode == 'REBUILD'):
        fail('STRATEGY_PLAN_SOURCE_CAMPAIGN_REQUIRED')
    if source_campaign is not None:
        _identifier(source_campaign)
    existing_campaign = selection['campaign_id']
    if (mode == 'APPEND') != (existing_campaign is not None):
        fail('STRATEGY_PLAN_CAMPAIGN_MODE')
    if existing_campaign is not None:
        _identifier(existing_campaign)
    budget = selection['campaign_budget']
    if (owner == 'CBO' and mode != 'APPEND') != (budget is not None):
        fail('STRATEGY_PLAN_WRONG_BUDGET_LEVEL')
    campaign = dict(name=_name(selection['campaign_name']), status='PAUSED', objective=objective,
                    buying_type='AUCTION', special_ad_categories=[])
    if owner == 'CBO' and mode != 'APPEND':
        campaign[field] = _money(budget, currency)
        campaign['bid_strategy'] = 'LOWEST_COST_WITHOUT_CAP'
    declared_minor = campaign.get(field, 0)
    if mode != 'APPEND':
        campaign['spend_cap'] = maximum_minor
    nodes = [dict(id='campaign', kind='CAMPAIGN', remote_id=existing_campaign, depends_on=[],
                  params=campaign if mode != 'APPEND' else None)]
    groups = selection['groups']
    if type(groups) is not list or not 1 <= len(groups) <= 6:
        fail('STRATEGY_PLAN_GROUP_COUNT')
    consumed = set()
    for index, group in enumerate(groups, 1):
        fields = ('name', 'adset_id', 'budget', 'targeting', 'assets')
        object_only(group, (*fields,'dynamic_media'), fields)
        identifier, amount = group['adset_id'], group['budget']
        if identifier is not None:
            _identifier(identifier)
            if mode != 'APPEND' or amount is not None or group['targeting'] is not None:
                fail('STRATEGY_PLAN_EXISTING_ADSET_FIELDS')
        elif (owner == 'ABO') != (amount is not None):
            fail('STRATEGY_PLAN_WRONG_BUDGET_LEVEL')
        adset_id = f'adset-{index}'
        params = None
        if identifier is None:
            params = dict(name=_name(group['name']), status='PAUSED', billing_event='IMPRESSIONS',
                          optimization_goal=optimization, destination_type='WEBSITE',
                          targeting=_target(group['targeting']),
                          promoted_object=dict(pixel_id=dataset, custom_event_type=event), **window_fields)
            if owner == 'ABO':
                params.update({field: _money(amount, currency), 'bid_strategy': 'LOWEST_COST_WITHOUT_CAP'})
                declared_minor += params[field]
        nodes.append(dict(id=adset_id, kind='ADSET', remote_id=identifier, depends_on=['campaign'], params=params))
        assets = group['assets']
        if type(assets) is not list or bool(assets) != (sequence == 'FULL'):
            fail('STRATEGY_PLAN_GROUP_ASSETS')
        dynamic = group.get('dynamic_media')
        if dynamic is not None:
            from .dynamic_creative import validate_assets, variants

            keys = ('images','alternate_primary_texts','alternate_headlines')
            object_only(dynamic,keys,keys)
            if sequence != 'FULL' or len(assets) != 1:
                fail('META_DYNAMIC_ADSET_REQUIRES_ONE_AD')
            validate_assets(dynamic['images'])
            variants(dynamic['alternate_primary_texts'],maximum=1024)
            variants(dynamic['alternate_headlines'],maximum=120)
            if params is not None:
                params['is_dynamic_creative'] = True
        for asset in assets:
            if type(asset) is not str or asset not in asset_ids or asset in consumed:
                fail('STRATEGY_PLAN_ASSET_SCOPE')
            consumed.add(asset)
            creative = f'creative-{len(consumed)}'
            entry = dict(id=creative, kind='CREATIVE', asset_sha256=asset, depends_on=[])
            if dynamic is not None:
                entry['params'] = dict(dynamic_media=dynamic)
            nodes.append(entry)
            nodes.append(dict(id=f'ad-{len(consumed)}', kind='AD', params={'status': 'PAUSED'},
                              depends_on=[adset_id, creative]))
    if consumed != set(asset_ids) or declared_minor > maximum_minor:
        fail('STRATEGY_PLAN_ASSET_OR_BUDGET_SCOPE')
    reference_ids = [node['remote_id'] for node in nodes if node.get('remote_id') is not None]
    if len(reference_ids) != len(set(reference_ids)):
        fail('STRATEGY_PLAN_REPEATED_PARENT')
    preview = dict(mode=mode, account_id=account, currency=currency, goal=goal, budget_owner=owner,
                   sequence=sequence,
                   source_campaign_id=source_campaign,
                   budget_schedule=schedule, approved_maximum_minor=maximum_minor,
                   allocated_budget_minor=declared_minor, nodes=nodes, requires_named_parent_readback=mode == 'APPEND',
                   network_used=False, dispatchable=False)
    return dict(preview, plan_sha256=digest(preview))


def managed_nodes(compiled, *, test_limit, daily_limit):
    """Select graph shapes supported by the managed paused creation pipeline."""
    if not test_limit or compiled['allocated_budget_minor'] > test_limit:
        return None
    if compiled['budget_schedule'] == 'DAILY' and (daily_limit is None or compiled['allocated_budget_minor'] > daily_limit):
        fail('STRATEGY_PLAN_DAILY_BUDGET_ABOVE_CASE_CAP')
    nodes = []
    for raw in compiled['nodes']:
        node = dict(raw)
        if node['kind'] == 'CAMPAIGN' and node['remote_id'] is None:
            params = dict(node['params'], spend_cap=test_limit)
            if compiled['budget_owner'] == 'ABO':
                params['is_adset_budget_sharing_enabled'] = False
            node['params'] = params
        nodes.append(node)
    return nodes
