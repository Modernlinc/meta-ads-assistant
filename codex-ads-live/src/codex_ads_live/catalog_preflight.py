"""Compare a merchant-supplied catalog export, set membership, and event IDs."""
import csv
from decimal import Decimal
from io import StringIO
from pathlib import Path
import re
from urllib.parse import urlsplit

from .business_profile import SECRET_PATTERN
from .journal import digest
from .local_paths import checked_path
from .meta_write_mapping import _write_new
from .monitor_tools import new_directory
from .operator_review import load_input
from .validation import fail, object_only, validate_https_destination

CATALOG_SOURCE = 'https://developers.facebook.com/docs/marketing-api/reference/product-catalog/'
REVIEW_SOURCE = 'https://secure.facebook.com/business/ads/review-policy-guidelines'
CATEGORY_SOURCE = 'https://secure.facebook.com/business/learn/video/advertising-guidelines'
TEMPLATE_SOURCE = 'https://developers.facebook.com/docs/marketing-api/reference/ad-creative-object-story-spec/'
FEED_FIELDS = ('id', 'title', 'link', 'image_link', 'availability', 'condition', 'price')
AVAILABILITY = ('in stock', 'out of stock', 'available for order', 'preorder', 'discontinued', 'pending')
POLICY_KINDS = ('CREATIVE', 'LANDING_PAGE', 'TARGETING', 'SPECIAL_CATEGORY', 'ACCOUNT', 'UNKNOWN')


def _items(source):
    if type(source) is not list or len(source) > 1000:
        fail('CATALOG_SKU_SCOPE')
    for item in source:
        if type(item) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', item):
            fail('CATALOG_SKU_SCOPE')
    return set(source)


def _feed(path, market_currency):
    source = checked_path(path, link_error='CATALOG_INPUT_PATH', volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
    if not source.is_file():
        fail('CATALOG_INPUT_PATH')
    with source.open('rb') as file:
        raw = file.read(1_000_001)
    if len(raw) > 1_000_000:
        fail('CATALOG_INPUT_BOUND')
    text = raw.decode('utf-8-sig')
    if SECRET_PATTERN.search(text):
        fail('CATALOG_SENSITIVE_CONTENT')
    reader = csv.DictReader(StringIO(text), strict=True)
    if (not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames))
            or not set(FEED_FIELDS) <= set(reader.fieldnames)):
        fail('CATALOG_FEED_HEADERS')
    rows, issues = {}, []
    for index, row in enumerate(reader, 2):
        if index > 1001 or None in row:
            fail('CATALOG_INPUT_BOUND')
        selected = {key: row[key] for key in FEED_FIELDS}
        identifier = row['id']
        _items([identifier])
        if identifier in rows:
            issues.append(dict(row=index, id=identifier, code='DUPLICATE_ID'))
            continue
        rows[identifier] = selected
        if type(row['title']) is not str or not 1 <= len(row['title'].strip()) <= 200:
            issues.append(dict(row=index, id=identifier, code='TITLE_MISSING_OR_TOO_LONG'))
        if row['availability'] not in AVAILABILITY:
            issues.append(dict(row=index, id=identifier, code='AVAILABILITY_VALUE'))
        if row['condition'] not in ('new', 'refurbished', 'used'):
            issues.append(dict(row=index, id=identifier, code='CONDITION_VALUE'))
        price = re.fullmatch(r'([0-9]{1,10}(?:\.[0-9]{1,2})?) ([A-Z]{3})', row['price'] or '')
        if price is None or price[2] != market_currency or Decimal(price[1]) <= 0:
            issues.append(dict(row=index, id=identifier, code='PRICE_OR_CURRENCY'))
        for name in ('link', 'image_link'):
            try:
                validate_https_destination(row[name], 'CATALOG_FEED_HTTPS')
                if len(row[name]) > 1500 or urlsplit(row[name]).fragment:
                    issues.append(dict(row=index, id=identifier, code=name.upper() + '_FORMAT'))
            except ValueError:
                issues.append(dict(row=index, id=identifier, code=name.upper() + '_FORMAT'))
    return rows, issues


def catalog(feed, data, output):
    keys = ('currency', 'product_set_ids', 'event_content_ids')
    object_only(data, (*keys,'meta_template'), keys)
    currency = data['currency']
    if type(currency) is not str or not re.fullmatch(r'[A-Z]{3}', currency):
        fail('CATALOG_CURRENCY_REQUIRED')
    expected = _items(data['product_set_ids'])
    event_ids = _items(data['event_content_ids'])
    rows, issues = _feed(feed, currency)
    unknown_set = sorted(expected - rows.keys())
    unknown_events = sorted(event_ids - rows.keys())
    events_outside_set = sorted(event_ids - expected) if expected else []
    state = 'REVIEW_FEED_AND_EVENT_IDS' if issues or unknown_set or unknown_events or events_outside_set else 'CATALOG_EXPORT_PREFLIGHT_READY'
    if not expected or not event_ids or not rows:
        state = 'CATALOG_MORE_LOCAL_EVIDENCE_REQUIRED'
    result = dict(status=state, currency=currency, feed_rows=len(rows), feed_sha256=digest(rows),
                  product_set_missing=unknown_set, event_content_ids_missing=unknown_events,
                  events_outside_selected_set=events_outside_set, issues=issues,
                  source_scope='MERCHANT_SUPPLIED_FEED_AND_EVENT_ID_SAMPLE',
                  source_urls=[CATALOG_SOURCE], platform_state='NOT_RUN', actions_authorized=False,
                  next_entry='media-buyer prepare')
    if 'meta_template' in data:
        template = _catalog_template(data['meta_template'],rows,expected)
        result['template_review'] = dict(template,ready_for_operator_review=state=='CATALOG_EXPORT_PREFLIGHT_READY')
        result['source_urls'].append(TEMPLATE_SOURCE)
    root = new_directory(output)
    _write_new(root, 'CATALOG-REVIEW.json', result)
    return result


def _catalog_template(value, rows, selected):
    from .native_rules import numeric

    fields = ('catalog_id','product_set_id','page_id','destination','main_text')
    object_only(value,fields,fields)
    catalog_id, set_id, page_id = (numeric(value[key]) for key in fields[:3])
    destination = value['destination']
    validate_https_destination(destination,'CATALOG_TEMPLATE_DESTINATION')
    if (len(destination)>1000 or any(c.isspace() or ord(c)<32 or c=='\\' for c in destination)
            or not selected or any(urlsplit(rows[sku]['link']).hostname != urlsplit(destination).hostname
                                  for sku in selected if sku in rows)):
        fail('CATALOG_TEMPLATE_DESTINATION')
    text = value['main_text']
    if type(text) is not str or not 1 <= len(text.strip()) <= 1000 or SECRET_PATTERN.search(text):
        fail('CATALOG_TEMPLATE_COPY')
    body = dict(product_set_id=set_id,object_story_spec=dict(page_id=page_id,template_data=dict(
        link=destination,message=text,name='{{product.name}}',
        call_to_action=dict(type='SHOP_NOW',value={'link':destination}))))
    return dict(catalog_id=catalog_id,product_set_id=set_id,request_fields=body,
                asset_mode='PLATFORM_CATALOG_TEMPLATE',platform_asset_relationship_verified=False,
                requirements=['CHECK_CATALOG_PRODUCT_SET_AND_PAGE_ASSIGNMENTS_IN_COMMERCE_MANAGER',
                              'CONFIRM_EVENTS_MATCH_SELECTED_CATALOG', 'REVIEW_CURRENT_ACCOUNT_AND_OBJECTIVE_ELIGIBILITY'],
                dispatchable=False)


def policy(data, output):
    keys = ('ad_id', 'observed_state', 'special_category', 'policy_issue', 'evidence_ref', 'copy_change')
    object_only(data, keys, keys)
    ad = data['ad_id']
    if ad is not None and (type(ad) is not str or not re.fullmatch(r'[1-9][0-9]{0,39}', ad)):
        fail('POLICY_AD_SCOPE')
    if data['observed_state'] not in ('PLANNING', 'REJECTED', 'RESTRICTED') or data['policy_issue'] not in POLICY_KINDS:
        fail('POLICY_REVIEW_SCOPE')
    category = data['special_category']
    if category not in ('NONE_REVIEWED', 'RELATED', 'UNRESOLVED'):
        fail('POLICY_REVIEW_SCOPE')
    if data['observed_state'] != 'PLANNING' and (ad is None or type(data['evidence_ref']) is not str
            or not re.fullmatch(r'EV_[A-Z0-9_]{1,80}', data['evidence_ref'])):
        fail('POLICY_FINDING_REFERENCE_REQUIRED')
    correction = data['copy_change']
    if correction is not None and (type(correction) is not str or not 1 <= len(correction.strip()) <= 1800
                                   or SECRET_PATTERN.search(correction)):
        fail('POLICY_CORRECTION_CONTENT')
    steps = ['核同一广告、落地页、视频和定向。保留素材版本和收到提示的日期。']
    if data['observed_state'] == 'RESTRICTED':
        steps.append('由账户所有人在 Business Support Home 核资产限制，按明确要求准备补件。')
    elif data['observed_state'] == 'REJECTED':
        steps.append('核具名拒审理由。有可判定问题时审改稿，认为平台决定有误时由所有人决定是否申请复审。')
    if category != 'NONE_REVIEWED':
        steps.append('按当前业务和市场核特殊广告类别，确认对应的定向与创建限制。')
    result = dict(status='POLICY_PREFLIGHT_PREPARED', ad_id=ad, local_steps=steps, correction_draft=correction,
                  source_issue=data['policy_issue'], source_evidence_ref=data['evidence_ref'],
                  special_category=category, issue_source='OPERATOR_SUPPLIED_CONTEXT',
                  dispatchable=False, platform_review='NOT_RUN', source_urls=[REVIEW_SOURCE, CATEGORY_SOURCE])
    _write_new(new_directory(output), 'POLICY-REVIEW.json', result)
    return result


def add_commands(sub):
    command = sub.add_parser('catalog-review', help='核对目录 feed、商品集与事件内容 ID')
    command.add_argument('--feed', required=True, type=Path)
    command.add_argument('--input', required=True, type=Path)
    command.add_argument('--output', required=True, type=Path)
    review = sub.add_parser('ad-policy-preflight', help='从具名审批或拒审证据准备可审改稿和下一步')
    review.add_argument('--input', required=True, type=Path)
    review.add_argument('--output', required=True, type=Path)
