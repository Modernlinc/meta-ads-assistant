"""Non-executable Meta write review packets. No credential or HTTP dependency.

These explicit mappings are derived from a pinned v26 SDK, not a verified v27
server contract. They never turn a declaration, hash or MOCK consent into a grant.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
import re
from zoneinfo import ZoneInfoNotFoundError
from .timezones import load_zone

from .journal import canonical, digest
from .local_paths import checked_path
from .validation import digest_scan_view, aware_time, fail, object_only, strict_json, validate_https_destination

SDK_COMMIT = 'b08e5c1d36a2adad3c4ede6578654c8eaca5f225'
SNAPSHOT_OPERATIONS = ('CREATE_CAMPAIGN', 'CREATE_ADSET', 'CREATE_AD', 'SET_ADSET_DAILY_BUDGET', 'SET_STATUS')
OPERATIONS = (*SNAPSHOT_OPERATIONS, 'CREATE_CREATIVE')
CREATIVE_CTAS = ('LEARN_MORE', 'SHOP_NOW')
SUPPORTED_CURRENCIES = ('USD', 'EUR', 'JPY')
WEBSITE_CONVERSION_OBJECTIVES = {'PURCHASE': 'OUTCOME_SALES', 'LEAD': 'OUTCOME_LEADS'}
OPTIONAL_PARAMETERS = {'CREATE_CAMPAIGN': ('spend_cap', 'daily_budget', 'lifetime_budget', 'bid_strategy',
                                               'is_adset_budget_sharing_enabled'),
                       'CREATE_ADSET': ('promoted_object', 'start_time', 'end_time', 'lifetime_budget',
                                        'daily_budget', 'bid_strategy', 'is_dynamic_creative'),
                       'CREATE_CREATIVE': ('description', 'video', 'media_scope_sha256', 'url_tags', 'carousel','dynamic_media')}
CREATE_EDGES = {'CREATE_CAMPAIGN': 'campaigns', 'CREATE_ADSET': 'adsets',
                'CREATE_AD': 'ads', 'CREATE_CREATIVE': 'adcreatives'}
PARAMETERS = {
    'CREATE_CAMPAIGN': ('name', 'objective', 'buying_type', 'special_ad_categories', 'is_adset_budget_sharing_enabled', 'status',
                        'spend_cap', 'daily_budget', 'lifetime_budget', 'bid_strategy'),
    'CREATE_ADSET': ('name', 'campaign_id', 'daily_budget', 'billing_event', 'optimization_goal', 'bid_strategy',
                     'destination_type', 'targeting', 'status', 'promoted_object', 'start_time', 'end_time', 'lifetime_budget',
                     'is_dynamic_creative'),
    'CREATE_AD': ('name', 'adset_id', 'creative', 'status'),
    'CREATE_CREATIVE': ('name', 'image_hash', 'destination_url', 'primary_text', 'headline', 'description',
                        'call_to_action', 'video', 'media_scope_sha256', 'url_tags', 'carousel','dynamic_media'),
    'SET_ADSET_DAILY_BUDGET': ('daily_budget',), 'SET_STATUS': ('status',),
}
CONTEXT = ('currency', 'timezone', 'observed_at', 'account_snapshot_sha256', 'target_snapshot_sha256',
           'parent_snapshot_sha256', 'creative_snapshot_sha256', 'material_sha256', 'page_id')
PROPOSAL_COMPARISON = 'proposal_comparison_sha256'
PROPOSAL_PACKET_REQUIRED = 'PROPOSAL_MATERIAL_PACKET_REQUIRED'
KEYS = ('schema', 'operation', 'api_version', 'account_id', 'target_id', 'target_level',
        'context', 'before', 'parameters', 'write_enabled')
OBJECTIVES = ('OUTCOME_TRAFFIC', 'OUTCOME_AWARENESS', 'OUTCOME_ENGAGEMENT', 'OUTCOME_LEADS', 'OUTCOME_SALES', 'OUTCOME_APP_PROMOTION')
PERMANENT_BLOCKERS = ('V27_WRITE_PROFILE_NOT_FULLY_VERIFIED', 'DECLARED_SNAPSHOTS_NOT_VERIFIED_READS',
                      'TRUSTED_HUMAN_WRITE_APPROVAL_UNAVAILABLE', 'REAL_WRITE_TRANSPORT_UNAVAILABLE',
                      'WRITE_GRANTS_AND_ASSET_BINDING_NOT_VERIFIED')


def utcnow(): return datetime.now(timezone.utc)


def blank_draft(operation):
    if operation not in OPERATIONS: fail('META_WRITE_OPERATION')
    level = {'CREATE_CAMPAIGN': 'CAMPAIGN', 'CREATE_ADSET': 'ADSET', 'CREATE_AD': 'AD', 'CREATE_CREATIVE': 'CREATIVE',
             'SET_ADSET_DAILY_BUDGET': 'ADSET'}.get(operation)
    return dict(schema='meta-write-draft-v1', operation=operation, api_version='v27.0', account_id=None,
                target_id=None, target_level=level, context={key: None for key in CONTEXT},
                before=dict(configured_status=None, daily_budget_minor=None),
                parameters={key: None for key in PARAMETERS[operation]}, write_enabled=False)


def _nullable_id(value):
    if value is not None and (type(value) is not str or not re.fullmatch('[1-9][0-9]{0,39}', value)):
        fail('META_WRITE_NUMERIC_ID')


def _minor(value):
    if value is not None and (type(value) is not int or not 1 <= value <= 100000000): fail('META_WRITE_MINOR_UNITS')


def _choice(value, allowed, code):
    if value is not None and (type(value) is not str or value not in allowed): fail(code)


GEO_AGE_KEYS = ('geo_locations', 'age_min', 'age_max')
AUDIENCE_KEYS = ('custom_audiences', 'excluded_custom_audiences')
PLACEMENT_FIELDS = {
    'facebook': frozenset(('feed', 'story', 'reels')),
    'instagram': frozenset(('stream', 'story', 'reels')),
}
PLACEMENT_KEYS = ('publisher_platforms', *(platform + '_positions' for platform in PLACEMENT_FIELDS))
ADVANCED_TARGETING_KEYS = (*AUDIENCE_KEYS, 'flexible_spec', *PLACEMENT_KEYS)


def _target_id_rows(values, *, returned=False):
    if type(values) is not list or not 1 <= len(values) <= 10:
        fail('META_WRITE_TARGETING_AUDIENCES')
    result = []
    for value in values:
        fields = ('id', 'name') if returned else ('id',)
        object_only(value, fields, ('id',))
        identifier = value['id']
        if identifier is None:
            fail('META_WRITE_NUMERIC_ID')
        _nullable_id(identifier)
        result.append(identifier)
    if len(result) != len(set(result)):
        fail('META_WRITE_TARGETING_AUDIENCES')
    return sorted(result)


def validate_targeting(target, *, advanced=False):
    fields = (*GEO_AGE_KEYS, *ADVANCED_TARGETING_KEYS) if advanced else GEO_AGE_KEYS
    object_only(target, fields, GEO_AGE_KEYS)
    object_only(target['geo_locations'], ('countries',), ('countries',))
    countries = target['geo_locations']['countries']
    if type(countries) is not list or not 1 <= len(countries) <= 10 or any(type(x) is not str or not re.fullmatch('[A-Z]{2}', x) for x in countries):
        fail('META_WRITE_COUNTRY_CODES')
    if len(set(countries)) != len(countries): fail('META_WRITE_COUNTRY_CODES')
    if any(type(target[key]) is not int or not 18 <= target[key] <= 65 for key in ('age_min', 'age_max')) or target['age_min'] > target['age_max']:
        fail('META_WRITE_AGE_PROFILE')
    audiences = [set(_target_id_rows(target[key])) if key in target else set() for key in AUDIENCE_KEYS]
    if audiences[0] & audiences[1]:
        fail('META_WRITE_TARGETING_AUDIENCE_OVERLAP')
    if 'flexible_spec' in target:
        groups = target['flexible_spec']
        if type(groups) is not list or len(groups) != 1:
            fail('META_WRITE_TARGETING_INTEREST_GROUP')
        object_only(groups[0], ('interests',), ('interests',))
        _target_id_rows(groups[0]['interests'])
    platforms = target.get('publisher_platforms', [])
    if type(platforms) is not list or any(type(platform) is not str or platform not in PLACEMENT_FIELDS for platform in platforms):
        fail('META_WRITE_TARGETING_PLACEMENT')
    if len(platforms) != len(set(platforms)):
        fail('META_WRITE_TARGETING_PLACEMENT')
    for platform, allowed in PLACEMENT_FIELDS.items():
        key = platform + '_positions'
        if (key in target) != (platform in platforms):
            fail('META_WRITE_TARGETING_PLACEMENT')
        if key in target:
            value = target[key]
            if (type(value) is not list or not value or any(type(position) is not str or position not in allowed for position in value)
                    or len(value) != len(set(value))):
                fail('META_WRITE_TARGETING_PLACEMENT')


def advanced_targeting(target, *, returned=False):
    """Compare selected IDs and configured placements without labels or order."""
    if type(target) is not dict:
        fail('META_WRITE_TARGETING_READBACK_REQUIRED')
    result = {}
    for key in AUDIENCE_KEYS:
        if target.get(key):
            result[key] = _target_id_rows(target[key], returned=returned)
    groups = target.get('flexible_spec', [])
    if groups:
        if type(groups) is not list or len(groups) != 1 or type(groups[0]) is not dict or set(groups[0]) != {'interests'}:
            if returned:
                return dict(result, unexpected_interest_shape=True)
            fail('META_WRITE_TARGETING_INTEREST_GROUP')
        result['interests'] = _target_id_rows(groups[0]['interests'], returned=returned)
    for key in PLACEMENT_KEYS:
        if target.get(key):
            values = target[key]
            if type(values) is not list or any(type(value) is not str for value in values):
                fail('META_WRITE_TARGETING_PLACEMENT')
            result[key] = sorted(values)
    return result


def validate_adset_parameters(params):
    _nullable_id(params['campaign_id'])
    if params.get('is_dynamic_creative') is not None and params['is_dynamic_creative'] is not True:
        fail('META_DYNAMIC_ADSET_SCOPE')
    start, end = params.get('start_time'), params.get('end_time')
    if (start is None) != (end is None) or (start is not None and aware_time(start) >= aware_time(end)):
        fail('META_WRITE_ADSET_WINDOW')
    if params.get('daily_budget') is not None and params.get('lifetime_budget') is not None:
        fail('META_WRITE_MUTUALLY_EXCLUSIVE_BUDGET')
    if params.get('lifetime_budget') is not None and end is None:
        fail('META_WRITE_ADSET_WINDOW')
    for key, allowed in dict(billing_event=('IMPRESSIONS',), optimization_goal=('LINK_CLICKS', 'OFFSITE_CONVERSIONS'),
                             bid_strategy=('LOWEST_COST_WITHOUT_CAP',), destination_type=('WEBSITE',)).items():
        _choice(params.get(key), allowed, 'META_WRITE_ADSET_PROFILE_NOT_IMPLEMENTED')
    if params['targeting'] is not None:
        validate_targeting(params['targeting'], advanced=True)
    promoted = params.get('promoted_object')
    if promoted is not None:
        if params['optimization_goal'] == 'LINK_CLICKS':
            fail('META_WRITE_TRAFFIC_PROMOTED_OBJECT_UNUSED')
        fields = ('pixel_id', 'custom_event_type')
        object_only(promoted, fields, fields)
        _nullable_id(promoted['pixel_id'])
        _choice(promoted['custom_event_type'], WEBSITE_CONVERSION_OBJECTIVES, 'META_WRITE_WEBSITE_EVENT_PROFILE')


def adset_parent_objective(params):
    if params['optimization_goal'] == 'LINK_CLICKS':
        return 'OUTCOME_TRAFFIC'
    if params['optimization_goal'] != 'OFFSITE_CONVERSIONS':
        return None
    promoted = params.get('promoted_object') or {}
    return WEBSITE_CONVERSION_OBJECTIVES.get(promoted.get('custom_event_type'))


def missing_website_event_fields(params):
    if params.get('optimization_goal') != 'OFFSITE_CONVERSIONS':
        return []
    promoted = params.get('promoted_object') or {}
    return ['parameters.promoted_object.' + key for key in ('pixel_id', 'custom_event_type')
            if promoted.get(key) is None]


def validate_url_tags(tags, destination=None):
    if type(tags) is not str or len(tags) > 512 or not tags:
        fail('META_WRITE_URL_TAGS_FORMAT')
    try:
        pairs = parse_qsl(tags, strict_parsing=True, keep_blank_values=True, max_num_fields=8)
    except ValueError:
        fail('META_WRITE_URL_TAGS_FORMAT')
    if not pairs or any(not re.fullmatch(r'utm_[a-z_]{1,24}', key)
                        or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}|\{\{(?:campaign|adset|ad)\.(?:id|name)\}\}', value)
                        for key, value in pairs):
        fail('META_WRITE_URL_TAGS_FORMAT')
    keys = {key for key, value in pairs}
    if len(keys) != len(pairs):
        fail('META_WRITE_URL_TAGS_DUPLICATED')
    if destination is not None:
        existing = {key.lower() for key, _ in parse_qsl(urlsplit(destination).query, keep_blank_values=True)}
        if existing & keys:
            fail('META_WRITE_URL_TAGS_DUPLICATED')


def validate_carousel(cards, destination=None):
    if type(cards) is not list or not 2 <= len(cards) <= 5:
        fail('META_WRITE_CAROUSEL_CARD_COUNT')
    used = set()
    for card in cards:
        fields = ('image_hash', 'link', 'name')
        object_only(card, fields, fields)
        image = card['image_hash']
        if type(image) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', image) or image in used:
            fail('META_WRITE_CAROUSEL_IMAGE_SCOPE')
        used.add(image)
        validate_https_destination(card['link'], 'META_WRITE_DESTINATION_URL')
        if (len(card['link']) > 1000 or any(c.isspace() for c in card['link'])
                or (destination is not None and urlsplit(card['link']).hostname != urlsplit(destination).hostname)):
            fail('META_WRITE_CAROUSEL_DESTINATION_SCOPE')
        title = card['name']
        if type(title) is not str or not 1 <= len(title.strip()) <= 120 or not title.isprintable():
            fail('META_WRITE_CAROUSEL_CARD_NAME')


def validate_creative_parameters(params):
    image = params['image_hash']
    if image is not None and (type(image) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,128}', image)):
        fail('META_WRITE_IMAGE_REFERENCE')
    video = params.get('video')
    media_scope = params.get('media_scope_sha256')
    if media_scope is not None and (type(media_scope) is not str or not re.fullmatch('[a-f0-9]{64}', media_scope)):
        fail('META_WRITE_MANAGED_MEDIA_SCOPE')
    if video is not None:
        object_only(video, ('video_id', 'thumbnail_hash'), ('video_id', 'thumbnail_hash'))
        thumbnail = video['thumbnail_hash']
        if (image is not None or media_scope is None or video['video_id'] is None
                or type(thumbnail) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,128}', thumbnail)):
            fail('META_WRITE_VIDEO_MEDIA_REFERENCE')
        _nullable_id(video['video_id'])
    url = params['destination_url']
    cards = params.get('carousel')
    dynamic = params.get('dynamic_media')
    if dynamic is not None:
        from .dynamic_creative import build

        if any(value is not None for value in (video,image,media_scope,cards)):
            fail('META_WRITE_CREATIVE_MEDIA_MODES_CONFLICT')
        if all(params.get(key) is not None for key in ('destination_url','primary_text','headline','description','call_to_action')):
            build(params)
    if cards is not None:
        if any(value is not None for value in (video, image, media_scope)):
            fail('META_WRITE_CREATIVE_MEDIA_MODES_CONFLICT')
        validate_carousel(cards, url)
    if url is not None:
        validate_https_destination(url, 'META_WRITE_DESTINATION_URL')
        if len(url) > 1000 or '\\' in url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
            fail('META_WRITE_DESTINATION_URL')
    if params.get('url_tags') is not None:
        validate_url_tags(params['url_tags'], url)
    for key, limit in (('primary_text', 2000), ('headline', 120), ('description', 500)):
        value = params[key]
        if value is not None and (type(value) is not str or (key != 'description' and not value.strip()) or len(value) > limit
                or any((ord(c) < 32 and c != '\n') or ord(c) == 127 for c in value)):
            fail('META_WRITE_CREATIVE_TEXT')
    _choice(params['call_to_action'], CREATIVE_CTAS, 'META_WRITE_CREATIVE_CALL_TO_ACTION')


def creative_request_parameters(params, page_id):
    url = params['destination_url']
    video = params.get('video')
    result = dict(name=params['name'])
    if params.get('url_tags') is not None:
        result['url_tags'] = params['url_tags']
    if params.get('dynamic_media') is not None:
        from .dynamic_creative import build

        return dict(result, object_story_spec={'page_id':page_id}, asset_feed_spec=build(params))
    if video is not None:
        video_data = dict(video_id=video['video_id'], image_hash=video['thumbnail_hash'],
                          message=params['primary_text'], title=params['headline'],
                          call_to_action=dict(type=params['call_to_action'], value={'link': url}))
        if params['description'] is not None:
            video_data['link_description'] = params['description']
        return dict(result, object_story_spec=dict(page_id=page_id, video_data=video_data))
    link_data = dict(link=url, message=params['primary_text'], name=params['headline'],
                     description=params['description'],
                     call_to_action=dict(type=params['call_to_action'], value={'link': url}))
    if params.get('carousel') is not None:
        link_data.update(child_attachments=params['carousel'], multi_share_optimized=False)
    else:
        link_data['image_hash'] = params['image_hash']
    return dict(result, object_story_spec=dict(page_id=page_id, link_data=link_data))


def validate_draft(value):
    object_only(value, KEYS, KEYS)
    encoded = canonical(value)
    if len(encoded.encode('utf-8')) > 16000: fail('META_WRITE_DRAFT_TOO_LARGE')
    hash_fields = (key for key in (*CONTEXT,PROPOSAL_COMPARISON) if key.endswith('_sha256'))
    scan = canonical({**value, 'context': digest_scan_view(value['context'], hash_fields)})
    if re.search(r'EAA[A-Za-z0-9]{40,}|sk-proj-|PRIVATE KEY|(?:access_token|app_secret|cookie)\s*[:=]', scan, re.I):
        fail('META_WRITE_SECRET_PATTERN_REFUSED')
    if value['schema'] != 'meta-write-draft-v1' or value['api_version'] != 'v27.0' or value['write_enabled'] is not False:
        fail('META_WRITE_PLANNING_ONLY')
    op = value['operation']
    if type(op) is not str or op not in OPERATIONS: fail('META_WRITE_OPERATION')
    _nullable_id(value['account_id']); _nullable_id(value['target_id'])
    _choice(value['target_level'], ('CAMPAIGN', 'ADSET', 'AD', 'CREATIVE') if op == 'CREATE_CREATIVE'
            else ('CAMPAIGN', 'ADSET', 'AD'), 'META_WRITE_TARGET_LEVEL')
    expected = blank_draft(op)['target_level']
    if expected is not None and value['target_level'] != expected: fail('META_WRITE_TARGET_LEVEL')
    creating = op.startswith('CREATE_')
    if creating and value['target_id'] is not None: fail('META_WRITE_SERVER_ASSIGNS_CREATED_ID')
    ctx = value['context']; object_only(ctx, (*CONTEXT,PROPOSAL_COMPARISON, 'parents_hold_budget', 'source_campaign_id'), CONTEXT)
    if 'parents_hold_budget' in ctx and (op != 'CREATE_ADSET' or ctx['parents_hold_budget'] is not True):
        fail('META_WRITE_BUDGET_SOURCE_SCOPE')
    if 'source_campaign_id' in ctx:
        if op != 'CREATE_CAMPAIGN' or ctx['source_campaign_id'] is None:
            fail('META_WRITE_REBUILD_SOURCE_SCOPE')
        _nullable_id(ctx['source_campaign_id'])
    if PROPOSAL_COMPARISON in ctx:
        comparison = ctx[PROPOSAL_COMPARISON]
        if op!='CREATE_AD' or type(comparison) is not str or not re.fullmatch('[a-f0-9]{64}',comparison):
            fail('META_WRITE_PROPOSAL_COMPARISON_HASH')
    _choice(ctx['currency'], SUPPORTED_CURRENCIES, 'META_WRITE_CURRENCY_PROFILE')
    if ctx['timezone'] is not None and (type(ctx['timezone']) is not str or len(ctx['timezone']) > 100 or
           not re.fullmatch('[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*', ctx['timezone'])): fail('META_WRITE_TIMEZONE_LABEL')
    if ctx['observed_at'] is not None: aware_time(ctx['observed_at'])
    for key in CONTEXT:
        if key.endswith('_sha256') and ctx[key] is not None and (type(ctx[key]) is not str or not re.fullmatch('[a-f0-9]{64}', ctx[key])):
            fail('META_WRITE_SNAPSHOT_HASH')
    _nullable_id(ctx['page_id'])
    before = value['before']; object_only(before, ('configured_status', 'daily_budget_minor'), ('configured_status', 'daily_budget_minor'))
    _choice(before['configured_status'], ('ACTIVE', 'PAUSED'), 'META_WRITE_BEFORE_STATUS'); _minor(before['daily_budget_minor'])
    if creating and any(x is not None for x in before.values()): fail('META_WRITE_CREATE_BEFORE_MUST_BE_EMPTY')
    if creating and ctx['target_snapshot_sha256'] is not None: fail('META_WRITE_UNUSED_TARGET_SNAPSHOT')
    if op not in ('CREATE_ADSET', 'CREATE_AD') and ctx['parent_snapshot_sha256'] is not None: fail('META_WRITE_UNUSED_PARENT_SNAPSHOT')
    activation = op == 'SET_STATUS' and isinstance(value['parameters'], dict) and value['parameters'].get('status') == 'ACTIVE'
    unused_creative = ('creative_snapshot_sha256', 'page_id') if activation or op == 'SET_ADSET_DAILY_BUDGET' else ('creative_snapshot_sha256', 'material_sha256', 'page_id')
    if op == 'CREATE_CREATIVE':
        unused_creative = ('creative_snapshot_sha256',)
    if op != 'CREATE_AD' and any(ctx[key] is not None for key in unused_creative):
        fail('META_WRITE_UNUSED_CREATIVE_CONTEXT')
    if op != 'SET_ADSET_DAILY_BUDGET' and before['daily_budget_minor'] is not None: fail('META_WRITE_UNUSED_BEFORE_BUDGET')
    params = value['parameters']
    keys = PARAMETERS[op]
    required_params = tuple(key for key in keys if key not in OPTIONAL_PARAMETERS.get(op, ()))
    object_only(params, keys, required_params)
    if op == 'CREATE_CREATIVE' and 'description' not in params:
        params = {**params, 'description': None}
        value = {**value, 'parameters': params}
    if 'name' in params and params['name'] is not None:
        name = params['name']
        if type(name) is not str or not name.strip() or len(name) > 120 or any(ord(c) < 32 for c in name): fail('META_WRITE_NAME')
    if 'status' in params:
        _choice(params['status'], ('PAUSED',) if creating else ('ACTIVE', 'PAUSED'), 'META_WRITE_STATUS')
    _minor(params.get('daily_budget'))
    _minor(params.get('lifetime_budget'))
    if op == 'CREATE_CAMPAIGN':
        _minor(params.get('spend_cap'))
        _choice(params['objective'], OBJECTIVES, 'META_WRITE_OBJECTIVE')
        _choice(params['buying_type'], ('AUCTION',), 'META_WRITE_BUYING_PROFILE')
        if params['special_ad_categories'] is not None and params['special_ad_categories'] != []:
            fail('META_WRITE_SPECIAL_CATEGORY_PROFILE_NOT_IMPLEMENTED')
        owns_budget = params.get('daily_budget') is not None or params.get('lifetime_budget') is not None
        if params.get('daily_budget') is not None and params.get('lifetime_budget') is not None:
            fail('META_WRITE_MUTUALLY_EXCLUSIVE_BUDGET')
        if owns_budget:
            if (params.get('is_adset_budget_sharing_enabled') is not None or
                    params.get('bid_strategy') != 'LOWEST_COST_WITHOUT_CAP'):
                fail('META_WRITE_CAMPAIGN_BUDGET_PROFILE')
        elif params.get('bid_strategy') is not None:
            fail('META_WRITE_CAMPAIGN_BUDGET_PROFILE')
        if params.get('is_adset_budget_sharing_enabled') not in (None, False):
            fail('META_WRITE_BUDGET_SHARING_PROFILE_NOT_IMPLEMENTED')
    elif op == 'CREATE_ADSET':
        validate_adset_parameters(params)
        if ctx.get('parents_hold_budget') and any(params.get(key) is not None for key in
                                                  ('daily_budget', 'lifetime_budget', 'bid_strategy')):
            fail('META_WRITE_BUDGET_SOURCE_SCOPE')
    elif op == 'CREATE_AD':
        _nullable_id(params['adset_id'])
        if params['creative'] is not None:
            object_only(params['creative'], ('creative_id',), ('creative_id',)); _nullable_id(params['creative']['creative_id'])
            if params['creative']['creative_id'] is None: fail('META_WRITE_CREATIVE_ID_REQUIRED')
    elif op == 'CREATE_CREATIVE':
        validate_creative_parameters(params)
    if len(canonical(value).encode('utf-8')) > 16000: fail('META_WRITE_DRAFT_TOO_LARGE')
    return value


def load_draft(path):
    path = checked_path(path, link_error='META_WRITE_PATH_LINK', volume_error='META_WRITE_VOLUME_NOT_MOUNTED')
    if not path.is_file() or path.stat().st_size > 16000: fail('META_WRITE_DRAFT_FILE')
    raw = path.read_bytes()
    if len(raw) > 16000: fail('META_WRITE_DRAFT_TOO_LARGE')
    return validate_draft(strict_json(raw.decode('utf-8-sig')))


def require_proposal_comparison(draft, comparison):
    expected = draft['context'].get(PROPOSAL_COMPARISON)
    if expected is not None and expected!=comparison:
        fail(PROPOSAL_PACKET_REQUIRED if comparison is None else 'PROPOSAL_COMPARISON_CHANGED')


def mapped_request(value, mapped):
    op = value['operation']
    edge = CREATE_EDGES.get(op)
    parameters = (creative_request_parameters(value['parameters'], value['context']['page_id'])
                  if op == 'CREATE_CREATIVE' else mapped)
    path = '/v27.0/' + ('act_' + value['account_id'] + '/' + edge if edge else value['target_id'])
    return dict(method='POST', path=path, parameters=deepcopy(parameters))


def review_draft(value, *, clock=utcnow):
    value = validate_draft(strict_json(canonical(value)))
    op = value['operation']; ctx = value['context']; params = value['parameters']; missing = []
    conversion = op == 'CREATE_ADSET' and params['optimization_goal'] == 'OFFSITE_CONVERSIONS'
    required = [('account_id', value['account_id']), ('target_level', value['target_level'])]
    control_fields = ('spend_cap', 'start_time', 'end_time', 'lifetime_budget','is_dynamic_creative')
    if op == 'CREATE_CAMPAIGN':
        control_fields += ('daily_budget', 'bid_strategy', 'is_adset_budget_sharing_enabled')
    elif op == 'CREATE_ADSET' and (ctx.get('parents_hold_budget') or params.get('lifetime_budget') is not None):
        control_fields += ('daily_budget', 'bid_strategy')
    required += [('parameters.' + key, item) for key, item in params.items()
                 if key != 'promoted_object' and not (key in control_fields and item is None)]
    if op == 'CREATE_CREATIVE' and any(params.get(key) is not None for key in ('video','carousel','dynamic_media')):
        required = [(key, item) for key, item in required if key != 'parameters.image_hash']
    if op == 'CREATE_CREATIVE':
        required = [(key, item) for key, item in required if key not in
                    ('parameters.video', 'parameters.media_scope_sha256', 'parameters.url_tags', 'parameters.carousel',
                     'parameters.dynamic_media')]
    context_required = ['currency', 'timezone', 'observed_at', 'account_snapshot_sha256']
    creating = op.startswith('CREATE_')
    if not creating:
        required += [('target_id', value['target_id']), ('before.configured_status', value['before']['configured_status'])]
        context_required.append('target_snapshot_sha256')
    if op == 'SET_ADSET_DAILY_BUDGET': required.append(('before.daily_budget_minor', value['before']['daily_budget_minor']))
    if op in ('CREATE_ADSET', 'CREATE_AD'): context_required.append('parent_snapshot_sha256')
    if op == 'CREATE_AD': context_required += ['creative_snapshot_sha256', 'material_sha256', 'page_id']
    if op == 'CREATE_CREATIVE': context_required += ['material_sha256', 'page_id']
    required += [('context.' + key, ctx[key]) for key in context_required]
    missing += [key for key, item in required if item is None]
    if op == 'CREATE_CAMPAIGN' and not any(params.get(key) is not None for key in ('daily_budget', 'lifetime_budget')):
        if params.get('is_adset_budget_sharing_enabled') is not False:
            missing.append('parameters.is_adset_budget_sharing_enabled')
    if op == 'CREATE_ADSET' and not ctx.get('parents_hold_budget'):
        if not any(params.get(key) is not None for key in ('daily_budget', 'lifetime_budget')):
            missing.append('parameters.daily_budget')
        if params.get('bid_strategy') is None:
            missing.append('parameters.bid_strategy')
    missing = list(dict.fromkeys(missing))
    if conversion:
        missing += missing_website_event_fields(params)
    blockers = list(PERMANENT_BLOCKERS); now = clock(); aware_time(now.isoformat())
    if ctx['observed_at'] is not None:
        age = now - aware_time(ctx['observed_at'])
        if not timedelta(0) <= age <= timedelta(minutes=5): blockers.append('DECLARED_CONTEXT_STALE_OR_FUTURE')
    if ctx['timezone'] is not None:
        try: load_zone(ctx['timezone'])
        except ZoneInfoNotFoundError: blockers.append('TIMEZONE_NAME_OR_LOCAL_DATABASE_UNRESOLVED')
    if op == 'CREATE_ADSET': blockers.append('PARENT_OBJECTIVE_AND_TARGETING_RULES_REQUIRE_CURRENT_RECHECK')
    if conversion: blockers.append('PIXEL_ACCESS_AND_WEBSITE_EVENT_REQUIRE_CURRENT_RECHECK')
    if op in ('CREATE_ADSET', 'SET_ADSET_DAILY_BUDGET'):
        blockers.append('CURRENCY_BUDGET_UNIT_AND_PLATFORM_MINIMUM_REQUIRE_RECHECK')
    if op == 'CREATE_AD': blockers.append('EXISTING_CREATIVE_PAGE_COPY_MEDIA_AND_RIGHTS_REQUIRE_RECHECK')
    if op == 'CREATE_CREATIVE': blockers.append('CREATIVE_ACCOUNT_IMAGE_PAGE_RIGHTS_AND_PREVIEW_REQUIRE_RECHECK')
    if PROPOSAL_COMPARISON in ctx: blockers.append(PROPOSAL_PACKET_REQUIRED)
    descendants = op == 'SET_ADSET_DAILY_BUDGET' or (op == 'SET_STATUS' and value['target_level'] in ('CAMPAIGN', 'ADSET'))
    if descendants: blockers.append('DESCENDANT_DELIVERY_IMPACT_REQUIRES_SEPARATE_REVIEW')
    request = None
    mapped = {key: item for key, item in params.items()
              if (key != 'promoted_object' and key not in control_fields) or item is not None}
    if not missing:
        request = mapped_request(value, mapped)
        mapped = request['parameters']
    if creating:
        diff = dict(before=None, after=deepcopy(mapped), new_id='ASSIGNED_BY_META_NOT_CALLER')
    elif op == 'SET_ADSET_DAILY_BUDGET':
        diff = dict(daily_budget_minor=dict(before=value['before']['daily_budget_minor'], after=params['daily_budget']),
                    configured_status=dict(before=value['before']['configured_status'], after=value['before']['configured_status']))
    else:
        diff = dict(configured_status=dict(before=value['before']['configured_status'], after=params['status']))
    no_change = not creating and ((op == 'SET_STATUS' and params['status'] is not None and params['status'] == value['before']['configured_status']) or
                   (op == 'SET_ADSET_DAILY_BUDGET' and params['daily_budget'] is not None and params['daily_budget'] == value['before']['daily_budget_minor']))
    if no_change: blockers.append('NO_CHANGE_DO_NOT_SEND')
    binding = dict(draft_sha256=digest(value), request=request, diff=diff, account_id=value['account_id'],
                   target_id=value['target_id'], target_level=value['target_level'], context=deepcopy(ctx),
                   impact=dict(configuration_effect='CREATE_NON_DELIVERING_CREATIVE_ASSET' if op == 'CREATE_CREATIVE' else
                               'CREATE_PAUSED_OBJECT' if creating else
                               'DAILY_BUDGET_ONLY_STATUS_UNCHANGED' if op == 'SET_ADSET_DAILY_BUDGET' else 'CONFIGURED_STATUS_ONLY',
                               configuration_fields=sorted(mapped), delivery_or_spend_may_change=not creating,
                               descendant_delivery_may_change=descendants,
                               effective_delivery_not_inferred=True),
                   source_sdk_commit=SDK_COMMIT, source_api_version='v26.0', planned_api_version='v27.0')
    return dict(schema='meta-write-review-v1', status='DRAFT_INCOMPLETE' if missing else 'MAPPING_REVIEW_READY_EXECUTION_BLOCKED',
                reviewed_at=now.isoformat(), operation=op, missing_fields=missing, blockers=blockers,
                review_sha256=digest(binding), approval_binding=binding, draft=value,
                amount_unit='NO_BUDGET_FIELDS' if op == 'CREATE_CREATIVE' else
                            'DECLARED_INTEGER_MINOR_UNITS_NO_CONVERSION_OR_SERVER_VALIDATION',
                credential_accessed=False, network_used=False, live_action_authorized=False,
                human_identity_verified=False, real_write_contract_verified=False, dispatchable=False)


def verify_review_binding(value, expected_sha256, *, clock=utcnow):
    review = review_draft(value, clock=clock)
    if type(expected_sha256) is not str or expected_sha256 != review['review_sha256']: fail('META_WRITE_REVIEW_CHANGED')
    return review  # Matching review remains non-executable and carries all blockers.


def _write_new(root, name, value):
    data = (canonical(value) + '\n').encode('utf-8')
    with (root / name).open('xb') as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())
    return dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def prepare_draft(operation, output):
    from .monitor_tools import new_directory
    value = blank_draft(operation); root = new_directory(output)
    row = _write_new(root, 'draft.json', value)
    _write_new(root, 'RECEIPT.json', dict(status='BLANK_WRITE_DRAFT_CREATED', files=[row], dispatchable=False))
    return dict(status='BLANK_WRITE_DRAFT_CREATED', operation=operation, dispatchable=False, network_used=False)


def export_review(value, output=None, *, material_directory=None, clock=utcnow):
    if material_directory is None:
        report = review_draft(value,clock=clock)
    else:
        from .material_bundle import attach_material_packet, load_material_packet
        packet = load_material_packet(material_directory,value,clock=clock)
        report = attach_material_packet(review_draft(value,clock=clock),packet)
    if output is not None:
        from .monitor_tools import new_directory
        root = new_directory(output); rows = [_write_new(root, 'REVIEW.json', report)]
        lines = ['# Meta写入参数候选审阅', '', '状态：' + report['status'],
                 '只做本地映射。下列POST描述没有执行；填写、hash或模拟批准均不授予真实权限。', '',
                 '审阅hash：' + report['review_sha256'], '缺项：' + ', '.join(report['missing_fields']),
                 '阻断：' + ', '.join(report['blockers']), '', '预算采用整数minor units；不自动换算、不验证平台最低预算。',
                 'SDK来源v26，候选路径v27合同仍未核实。特殊类别/其他Ad Set配置及新素材创建待实现。',
                 '完整候选参数、前后差异、声明快照及绑定字段见REVIEW.json。']
        sections = [('候选请求（未发送）', report['approval_binding']['request']),
                    ('前后差异', report['approval_binding']['diff']),
                    ('投放影响待核', report['approval_binding']['impact']),
                    ('声明的证据上下文（未验真）', report['approval_binding']['context'])]
        if 'material_evidence' in report:
            sections += [('本地材料审阅证据',report['material_evidence']),('当前材料待核项',report['current_material_findings'])]
        for heading,item in sections:
            lines += ['', '## ' + heading, '', '```json', json.dumps(item, ensure_ascii=False, indent=2), '```']
        data = ('\n'.join(lines) + '\n').encode('utf-8')
        with (root / 'REVIEW.md').open('xb') as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        rows.append(dict(path='REVIEW.md', bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
        _write_new(root, 'RECEIPT.json', dict(status=report['status'], files=rows, dispatchable=False, real_meta_requests=0))
    return report


def draft_schema():
    def obj(properties):
        return dict(type='object', additionalProperties=False, required=list(properties), properties=properties)
    identifier = dict(type=['string', 'null'], pattern='^[1-9][0-9]{0,39}$')
    amount = dict(type=['integer', 'null'], minimum=1, maximum=100000000)
    def choice(values): return dict(enum=[None, *values])
    context = {key: dict(type=['string', 'null'], pattern='^[a-f0-9]{64}$') for key in CONTEXT if key.endswith('_sha256')}
    context.update(currency=choice(SUPPORTED_CURRENCIES), timezone=dict(type=['string', 'null'], maxLength=100,
        pattern='^[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*$'), observed_at=dict(type=['string', 'null'], format='date-time'), page_id=identifier)
    target = obj(dict(geo_locations=obj(dict(countries=dict(type='array', minItems=1, maxItems=10, uniqueItems=True,
                    items=dict(type='string', pattern='^[A-Z]{2}$')))), age_min=dict(type='integer', minimum=18, maximum=65),
                    age_max=dict(type='integer', minimum=18, maximum=65)))
    target['type'] = ['object', 'null']
    target_ids = dict(type='array', minItems=1, maxItems=10,
                      items=obj(dict(id={**identifier, 'type':'string'})))
    target['properties'].update({key: target_ids for key in AUDIENCE_KEYS})
    target['properties']['flexible_spec'] = dict(type='array', minItems=1, maxItems=1,
                                                 items=obj(dict(interests=target_ids)))
    target['properties']['publisher_platforms'] = dict(type='array', uniqueItems=True,
                                                       items=dict(enum=sorted(PLACEMENT_FIELDS)))
    for platform, positions in PLACEMENT_FIELDS.items():
        target['properties'][platform + '_positions'] = dict(type='array', minItems=1, uniqueItems=True,
                                                             items=dict(enum=sorted(positions)))
    creative = obj(dict(creative_id={**identifier, 'type': 'string'})); creative['type'] = ['object', 'null']
    video = obj(dict(video_id={**identifier, 'type': 'string'},
                     thumbnail_hash=dict(type='string', pattern='^[A-Za-z0-9_-]{1,128}$')))
    video['type'] = ['object', 'null']
    promoted = obj(dict(pixel_id=identifier, custom_event_type=choice(WEBSITE_CONVERSION_OBJECTIVES)))
    promoted['type'] = ['object', 'null']
    fields = dict(name=dict(type=['string', 'null'], minLength=1, maxLength=120), objective=choice(OBJECTIVES),
                  buying_type=choice(('AUCTION',)), special_ad_categories=dict(type=['array', 'null'], maxItems=0),
                  is_adset_budget_sharing_enabled=dict(enum=[None, False]), status=choice(('PAUSED', 'ACTIVE')),
                  campaign_id=identifier, adset_id=identifier, daily_budget=amount, billing_event=choice(('IMPRESSIONS',)),
                  spend_cap=amount, lifetime_budget=amount, start_time=dict(type=['string', 'null'], format='date-time'),
                  end_time=dict(type=['string', 'null'], format='date-time'),
                  optimization_goal=choice(('LINK_CLICKS', 'OFFSITE_CONVERSIONS')), bid_strategy=choice(('LOWEST_COST_WITHOUT_CAP',)),
                  destination_type=choice(('WEBSITE',)), targeting=target, creative=creative, promoted_object=promoted,
                  video=video, media_scope_sha256=dict(type=['string', 'null'], pattern='^[a-f0-9]{64}$'),
                  image_hash=dict(type=['string', 'null'], pattern='^[A-Za-z0-9_-]{1,128}$'),
                  destination_url=dict(type=['string', 'null'], pattern='^https://', maxLength=1000),
                  primary_text=dict(type=['string', 'null'], minLength=1, maxLength=2000),
                  headline=dict(type=['string', 'null'], minLength=1, maxLength=120),
                  description=dict(type=['string', 'null'], maxLength=500),
                  call_to_action=choice(CREATIVE_CTAS), url_tags=dict(type=['string','null'], maxLength=512))
    fields['carousel'] = dict(type=['array','null'], minItems=2, maxItems=5,
                              items=obj(dict(image_hash={**fields['image_hash'], 'type':'string'},
                                             link={**fields['destination_url'], 'type':'string'},
                                             name={**fields['headline'], 'type':'string'})))
    fields['is_dynamic_creative'] = dict(enum=[True,None])
    fields['dynamic_media'] = obj(dict(
        images=dict(type='array',minItems=2,maxItems=5,items={**fields['image_hash'],'type':'string'}),
        alternate_primary_texts=dict(type='array',minItems=1,maxItems=4,items=dict(type='string',maxLength=1024)),
        alternate_headlines=dict(type='array',minItems=1,maxItems=4,items=dict(type='string',maxLength=120))))
    fields['dynamic_media']['type'] = ['object','null']
    variants = []
    for op in OPERATIONS:
        params = {key: fields[key] for key in PARAMETERS[op]}
        if op.startswith('CREATE_') and 'status' in params: params['status'] = choice(('PAUSED',))
        context_schema = obj(dict(context))
        if op == 'CREATE_CAMPAIGN':
            context_schema['properties']['source_campaign_id'] = {**identifier, 'type': 'string'}
        if op == 'CREATE_ADSET':
            context_schema['properties']['parents_hold_budget'] = {'const': True}
        if op=='CREATE_AD':
            context_schema['properties'][PROPOSAL_COMPARISON] = dict(type='string',pattern='^[a-f0-9]{64}$')
        parameters_schema = obj(params)
        for key in OPTIONAL_PARAMETERS.get(op, ()):
            parameters_schema['required'].remove(key)
        if op == 'CREATE_CREATIVE':
            parameters_schema['allOf'] = [{
                'if': {'required':['video'], 'properties':{'video':{'type':'object'}}},
                'then': {'required':['media_scope_sha256'], 'properties':{
                    'media_scope_sha256':{'type':'string'}, 'image_hash':{'type':'null'}}}
            }]
        props = dict(schema={'const': 'meta-write-draft-v1'}, operation={'const': op}, api_version={'const': 'v27.0'},
                     account_id=identifier, target_id={'type': 'null'} if op.startswith('CREATE_') else identifier,
                     target_level={'const': blank_draft(op)['target_level']} if op != 'SET_STATUS' else choice(('CAMPAIGN', 'ADSET', 'AD')),
                     context=context_schema, before=obj(dict(configured_status=choice(('PAUSED', 'ACTIVE')), daily_budget_minor=amount)),
                     parameters=parameters_schema, write_enabled={'const': False})
        variants.append(obj(props))
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'Non-executable bounded Meta write parameter draft',
            'oneOf': variants, '$comment': 'Incomplete null values are valid preparation. Runtime additionally checks ordered ages, unused context, names/secret patterns, 16000-byte limit and aware timestamps. Complete declarations never authorize a request. SDK v26 mappings and partial v27 public references are not complete server-contract verification; existing MOCK approvals cannot approve these packets.'}
