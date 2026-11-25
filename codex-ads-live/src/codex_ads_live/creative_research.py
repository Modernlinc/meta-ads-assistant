"""Workspace-bound research records. Public observations and own-account evidence stay separate."""

from copy import deepcopy
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from .client_workspace import draft_review_scope, journal_for, load, workspace_identity
from .journal import Journal, canonical, digest
from .local_paths import checked_path
from .validation import aware_time, fail, object_only, strict_json, validate_https_destination


REF = re.compile(r'[A-Za-z0-9_-]{1,64}')
SHA = re.compile(r'[a-f0-9]{64}')
DIMENSIONS = {
    'selling_point': {'VISUAL','SPOKEN','SUBTITLE','SCRIPT'},
    'hook': {'VISUAL','SPOKEN','SUBTITLE','SCRIPT'},
    'conversion_point': {'VISUAL','SPOKEN','SUBTITLE','SCRIPT'},
    'human_feel': {'VISUAL'},
}
SECRET = re.compile(r'EAA[A-Za-z0-9]{35,}|sk-proj-|PRIVATE KEY|(?:access_token|app_secret|cookie)\s*[:=]', re.I)


def _value(path):
    path = checked_path(Path(path), link_error='RESEARCH_INPUT_LINK',
                        volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
    if not path.is_file() or path.stat().st_size > 192000:
        fail('RESEARCH_INPUT_EXPECTED_BOUNDED_JSON')
    with path.open('rb') as handle:
        body = handle.read(192001)
    try:
        text = body.decode('utf-8')
    except UnicodeError:
        fail('RESEARCH_INPUT_INVALID')
    if len(body) > 192000 or SECRET.search(text):
        fail('RESEARCH_INPUT_INVALID')
    return strict_json(text, max_chars=192000)


def _ref(value):
    if type(value) is not str or not REF.fullmatch(value):
        fail('RESEARCH_REFERENCE_INVALID')
    return value


def _short(value, maximum=2400):
    if type(value) is not str or not value.strip() or len(value)>maximum or SECRET.search(value):
        fail('RESEARCH_TEXT_INVALID')
    return value


def _fixed(value, keys):
    object_only(value,keys,keys)


def _observations(value, limit=50):
    if type(value) is not list or len(value)>limit:
        fail('RESEARCH_INPUT_TOO_MANY_ITEMS')
    return value


def _public_url(value, archive_id=None):
    validate_https_destination(value,'PUBLIC_OBSERVATION_HTTPS_REQUIRED')
    url=urlsplit(value)
    if url.hostname not in ('facebook.com','www.facebook.com','m.facebook.com') or url.path.rstrip('/') != '/ads/library':
        fail('PUBLIC_OBSERVATION_SOURCE_REQUIRED')
    if archive_id is not None and parse_qs(url.query).get('id') != [archive_id]:
        fail('PUBLIC_OBSERVATION_URL_ID_MISMATCH')
    return value


def _public_content(row):
    stable = {key: value for key, value in row.items() if key not in ('observed_at', 'detail')}
    stable.update(delivery_start=row.get('delivery_start'), delivery_stop=row.get('delivery_stop'))
    detail = row.get('detail')
    stable['detail'] = None if detail is None else {key: value for key, value in detail.items() if key != 'observed_at'}
    return stable


def add_public_record(entries, row):
    identifier = (row['page_id'], row['ad_archive_id'])
    previous = entries.get(identifier)
    if previous is None:
        entries[identifier] = row
    elif _public_content(previous) != _public_content(row):
        fail('PUBLIC_OBSERVATION_DUPLICATE_CONFLICT')


def _normalize_public(value):
    _fixed(value,('scope','records','coverage'))
    scope=value['scope']
    _fixed(scope,('query','countries','status','date_min','date_max'))
    _short(scope['query'],160)
    countries=_observations(scope['countries'],10)
    if not countries or any(type(c) is not str or not re.fullmatch('[A-Z]{2}',c) for c in countries):
        fail('PUBLIC_OBSERVATION_COUNTRIES_REQUIRED')
    from datetime import date
    for key in ('date_min','date_max'):
        if scope[key] is not None and date.fromisoformat(scope[key]).isoformat()!=scope[key]:
            fail('PUBLIC_OBSERVATION_DATE_INVALID')
    if scope['date_min'] and scope['date_max'] and scope['date_min']>scope['date_max']:
        fail('PUBLIC_OBSERVATION_DATE_INVALID')
    if scope['status'] not in ('ACTIVE','INACTIVE','ALL') or value['coverage'] not in ('SAMPLE','TRUNCATED'):
        fail('PUBLIC_OBSERVATION_SCOPE_INVALID')
    entries={}
    for row in _observations(value['records'],50):
        keys=('ad_archive_id','page_id','source_url','observed_at','body','detail','angle_hypothesis')
        details = ('delivery_start','delivery_stop')
        object_only(row, (*keys, *details), keys)
        for key in ('ad_archive_id','page_id'):
            if type(row[key]) is not str or not re.fullmatch('[0-9]{1,40}',row[key]):
                fail('PUBLIC_OBSERVATION_ID_REQUIRED')
        _public_url(row['source_url'], row['ad_archive_id'])
        aware_time(row['observed_at'])
        for field in details:
            day = row.get(field)
            if day is not None and (type(day) is not str or date.fromisoformat(day).isoformat() != day):
                fail('PUBLIC_OBSERVATION_DATE_INVALID')
        for key in ('body','angle_hypothesis'):
            if row[key] is not None:_short(row[key])
        detail=row['detail']
        if detail is not None:
            _fixed(detail,('ad_archive_id','page_id','source_url','observed_at','body'))
            if any(detail[k]!=row[k] for k in ('ad_archive_id','page_id')):
                fail('PUBLIC_OBSERVATION_DETAIL_MISMATCH')
            _public_url(detail['source_url'], detail['ad_archive_id'])
            aware_time(detail['observed_at'])
            if detail['body'] is not None:_short(detail['body'])
        add_public_record(entries, row)
    return dict(scope=scope,records=list(entries.values()),coverage=value['coverage'],
                own_account_metrics=False,source_type='PUBLIC_AD_OBSERVATIONS')


def public_observations(workspace,account,path):
    if path is None:fail('PUBLIC_OBSERVATION_INPUT_REQUIRED')
    document = _value(path)
    if type(document) is dict and document.get('status') == 'PUBLIC_AD_QUERY_RETURNED':
        document = {key: document['observation'][key] for key in ('scope','records','coverage')}
    normalized=_normalize_public(document)
    ws=journal_for(workspace,existing_only=True)
    with ws.locked(existing_only=True):
        profile=load(ws)
        scope=draft_review_scope(profile,account)
        return dict(normalized,account_id=scope['account_id'],workspace_identity_sha256=workspace_identity(profile),
                    evidence_sha256=digest(normalized),network_used=False)


def _apply(state, update):
    data=deepcopy(state)
    _fixed(update,('brand','sources','concepts','learning','reviews'))
    if update['brand'] is not None:
        _fixed(update['brand'],('positioning','voice'))
        for value in update['brand'].values(): _short(value,500)
        data['brand']=update['brand']
    for entry in _observations(update['sources']):
        _fixed(entry,('id','kind','url','observed_at','product','fact'))
        name=_ref(entry['id'])
        if entry['kind'] not in ('CUSTOMER_LANGUAGE','OWN_PERFORMANCE','PUBLIC_OBSERVATION','PRODUCT_FACT'):
            fail('RESEARCH_SOURCE_TYPE')
        if entry['kind']=='PUBLIC_OBSERVATION':_public_url(entry['url'])
        elif entry['url'] is not None:validate_https_destination(entry['url'],'RESEARCH_SOURCE_HTTPS_REQUIRED')
        aware_time(entry['observed_at']);_short(entry['product'],180);_short(entry['fact'])
        prior=data['sources'].get(name)
        if prior is not None and prior!=entry:fail('RESEARCH_SOURCE_FROZEN')
        data['sources'][name]=entry
    for entry in _observations(update['concepts']):
        _fixed(entry,('id','source_ids','hypothesis','variants','state'))
        name=_ref(entry['id'])
        _short(entry['hypothesis'])
        refs=_observations(entry['source_ids'],12)
        if not refs or any(_ref(ref) not in data['sources'] for ref in refs):
            fail('RESEARCH_CONCEPT_SOURCE_REQUIRED')
        variants=_observations(entry['variants'],12)
        if not variants:fail('RESEARCH_CONCEPT_VARIANT_REQUIRED')
        names=set()
        for variant in variants:
            _fixed(variant,('id','brief'))
            names.add(_ref(variant['id']))
            _short(variant['brief'])
        if len(names)!=len(variants) or entry['state'] not in ('AVAILABLE','USED'):
            fail('RESEARCH_CONCEPT_STATE')
        previous=data['concepts'].get(name)
        if previous and previous['state']=='USED' and previous!=entry:
            fail('RESEARCH_USED_CONCEPT_FROZEN')
        data['concepts'][name]=entry
    for entry in _observations(update['learning']):
        _fixed(entry,('id','source_id','scope','finding','state'))
        name=_ref(entry['id'])
        if _ref(entry['source_id']) not in data['sources'] or entry['state'] not in ('WATCH','ACTIVE','RETIRED'):
            fail('RESEARCH_LEARNING_EVIDENCE_REQUIRED')
        _short(entry['scope'],180);_short(entry['finding'])
        data['learning'][name]=entry
    for entry in _observations(update['reviews']):
        review=_media_review(entry,data)
        data['reviews'][review['asset_sha256']]=review
    if any(len(data[x])>200 for x in ('sources','concepts','learning','reviews')):
        fail('RESEARCH_WORKSPACE_LIMIT')
    if len(canonical(data).encode('utf-8')) > 850_000:
        fail('RESEARCH_WORKSPACE_LIMIT')
    return data


def _media_review(entry,data):
    _fixed(entry,('asset_sha256','concept_id','observations','comments','weakest','suggestion'))
    asset=entry['asset_sha256']
    if type(asset) is not str or not SHA.fullmatch(asset):
        fail('RESEARCH_MATERIAL_HASH_REQUIRED')
    if _ref(entry['concept_id']) not in data['concepts']:
        fail('RESEARCH_CONCEPT_REQUIRED')
    seen={}
    for obs in _observations(entry['observations'],20):
        _fixed(obs,('id','asset_sha256','kind','evidence_ref','time_ms'))
        name=_ref(obs['id'])
        if (name in seen or obs['asset_sha256']!=asset or obs['kind'] not in ('VISUAL','SPOKEN','SUBTITLE','SCRIPT')
                or (obs['time_ms'] is not None and (type(obs['time_ms']) is not int or obs['time_ms']<0))):
            fail('RESEARCH_MATERIAL_OBSERVATION_MISMATCH')
        _ref(obs['evidence_ref'])
        seen[name]=obs
    _fixed(entry['comments'],tuple(DIMENSIONS))
    facts={}
    for dimension,comment in entry['comments'].items():
        if comment is None:
            facts[dimension]={'state':'NOT_OBSERVED'}
            continue
        _fixed(comment,('observation_id','comment'))
        observation=seen.get(_ref(comment['observation_id']))
        if observation is None or observation['kind'] not in DIMENSIONS[dimension]:
            fail('RESEARCH_MATERIAL_COMMENT_NO_MATCHING_EVIDENCE')
        _short(comment['comment'])
        facts[dimension]=dict(comment,state='OBSERVED')
    weakest=entry['weakest']
    if weakest is not None and (weakest not in facts or facts[weakest]['state']!='OBSERVED'):
        fail('RESEARCH_MATERIAL_WEAKEST_UNOBSERVED')
    if entry['suggestion'] is not None:_short(entry['suggestion'])
    return dict(entry,comments=facts,media_decoded=False,rights_verified=False)


def _record(workspace_journal, profile, account_id, update=None):
    identity = dict(schema='creative-research-v1', workspace_identity_sha256=workspace_identity(profile),
                    account_id=account_id)
    initial = dict(identity, brand=None, sources={}, concepts={}, learning={}, reviews={})
    location = Path(workspace_journal.directory) / 'creative-research' / account_id
    if update is None and not location.exists() and not location.is_symlink():
        return initial
    journal = Journal(location, stage_snapshots=True, existing_only=update is None)

    def validate(row):
        _fixed(row, tuple(initial))
        if any(row[key] != value for key, value in identity.items()):
            fail('RESEARCH_WORKSPACE_SCOPE_CHANGED')

    with journal.locked(existing_only=update is None):
        record, damaged = journal.load(validate)
        if damaged or journal.unpublished_snapshot_count():
            fail('RESEARCH_WORKSPACE_REVIEW_REQUIRED')
        current = record if record is not None else initial
        if update is not None:
            revised = _apply(current, update)
            if revised != current:
                journal.save(revised, validate)
            current = revised
        return current


def freeze_selection(workspace_journal, profile, account_id, concept_id, variant_id, asset_sha256):
    state = _record(workspace_journal, profile, account_id)
    concept = state['concepts'].get(_ref(concept_id))
    review = state['reviews'].get(asset_sha256)
    if (concept is None or concept['state'] != 'AVAILABLE' or review is None
            or review['concept_id'] != concept_id):
        fail('RESEARCH_PLAN_SELECTION_MISMATCH')
    variant = next((row for row in concept['variants'] if row['id'] == _ref(variant_id)), None)
    if variant is None:
        fail('RESEARCH_PLAN_VARIANT_MISMATCH')
    return dict(concept_id=concept_id, variant_id=variant_id, asset_sha256=asset_sha256,
                review_sha256=digest(review), brief_sha256=digest(variant),
                source_sha256=digest([state['sources'][key] for key in concept['source_ids']]))


def import_public_sources(document, profile, account_id, product):
    _short(product, 180)
    if (document.get('account_id') != account_id
            or document.get('workspace_identity_sha256') != workspace_identity(profile)):
        fail('PUBLIC_OBSERVATION_WORKSPACE_SCOPE_MISMATCH')
    normalized = _normalize_public({key: document[key] for key in ('scope','records','coverage')})
    if digest(normalized) != document.get('evidence_sha256'):
        fail('PUBLIC_OBSERVATION_RECEIPT_MISMATCH')
    return [
        dict(id='LIB-' + digest([row['page_id'], row['ad_archive_id'], row['observed_at'], row['body']])[:40],
             kind='PUBLIC_OBSERVATION', url=row['source_url'], observed_at=row['observed_at'], product=product,
             fact=row['body'])
        for row in normalized['records'] if row['body'] is not None
    ]


def run_research(workspace, account, path, *, public_path=None, product=None):
    if (public_path is None) != (product is None):
        fail('PUBLIC_OBSERVATION_PRODUCT_AND_RECEIPT_REQUIRED')
    update = _value(path) if path is not None else None
    raw_public = _value(public_path) if public_path is not None else None
    ws = journal_for(workspace, existing_only=True)
    with ws.locked(existing_only=True):
        profile = load(ws)
        account_scope = draft_review_scope(profile, account)
        if raw_public is not None:
            if update is not None:
                _fixed(update, ('brand','sources','concepts','learning','reviews'))
            else:
                update = dict(brand=None, sources=[], concepts=[], learning=[], reviews=[])
            update['sources'] = [*_observations(update['sources']), *import_public_sources(
                raw_public, profile, account_scope['account_id'], product)]
        current = _record(ws, profile, account_scope['account_id'], update)
        return dict(status='CREATIVE_RESEARCH_READY',research=current,source_sha256=digest(current),
                    business_profile=account_scope['business_profile'],
                    available_concepts=[r for r in current['concepts'].values() if r['state']=='AVAILABLE'],
                    network_used=False,write_authorized=False)
