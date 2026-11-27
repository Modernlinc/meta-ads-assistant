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
SECRET = re.compile(r'(?-i:EAA[A-Za-z0-9]{35,})|sk-proj-|PRIVATE KEY|(?:access_token|app_secret|cookie)\s*[:=]', re.I)


def _value(path):
    path = checked_path(Path(path), link_error='RESEARCH_INPUT_LINK',
                        volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
    if not path.is_file() or path.stat().st_size > 192000:
        fail('RESEARCH_INPUT_EXPECTED_BOUNDED_JSON')
    with path.open('rb') as handle:
        body = handle.read(192001)
    try:
        text = body.decode('utf-8-sig')
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
            detail_required = ('ad_archive_id', 'page_id', 'source_url', 'observed_at', 'body')
            object_only(detail, (*detail_required, 'additional_observations'), detail_required)
            if 'additional_observations' in detail:
                _public_detail(detail['additional_observations'])
            if any(detail[k]!=row[k] for k in ('ad_archive_id','page_id')):
                fail('PUBLIC_OBSERVATION_DETAIL_MISMATCH')
            _public_url(detail['source_url'], detail['ad_archive_id'])
            aware_time(detail['observed_at'])
            if detail['body'] is not None:_short(detail['body'])
        add_public_record(entries, row)
    return dict(scope=scope,records=list(entries.values()),coverage=value['coverage'],
                own_account_metrics=False,source_type='PUBLIC_AD_OBSERVATIONS')



def _public_detail(detail):
    graph_fields = ('titles', 'descriptions', 'captions', 'publisher_platforms', 'snapshot_available')
    host_fields = ('call_to_action', 'destination', 'media_observations')
    object_only(detail, ('basis', *graph_fields, *host_fields), ('basis',))
    if detail['basis'] not in ('GRAPH_ADS_ARCHIVE', 'HOST_PUBLIC_OBSERVATION'):
        fail('PUBLIC_OBSERVATION_DETAIL_BASIS')
    if detail['basis'] == 'GRAPH_ADS_ARCHIVE' and any(key in detail for key in host_fields):
        fail('PUBLIC_OBSERVATION_DETAIL_BASIS')
    for field in ('titles', 'descriptions', 'captions'):
        if field in detail:
            for value in _observations(detail[field], 8):
                _short(value, 800)
    if 'publisher_platforms' in detail:
        for platform in _observations(detail['publisher_platforms'], 16):
            if type(platform) is not str or not re.fullmatch('[a-z_]{2,40}', platform):
                fail('PUBLIC_OBSERVATION_DETAIL_PLATFORMS')
    if 'snapshot_available' in detail and type(detail['snapshot_available']) is not bool:
        fail('PUBLIC_OBSERVATION_DETAIL_SNAPSHOT')
    if 'call_to_action' in detail:
        _short(detail['call_to_action'], 140)
    if 'destination' in detail:
        validate_https_destination(detail['destination'], 'PUBLIC_OBSERVATION_DESTINATION')
    if 'media_observations' in detail:
        for item in _observations(detail['media_observations'], 8):
            _fixed(item, ('evidence_ref', 'asset_sha256', 'observation'))
            _ref(item['evidence_ref'])
            if type(item['asset_sha256']) is not str or not SHA.fullmatch(item['asset_sha256']):
                fail('PUBLIC_OBSERVATION_DETAIL_MEDIA')
            _short(item['observation'], 800)


def _usable_public_fact(row):
    detail = row.get('detail') or {}
    extra = detail.get('additional_observations') or {}
    snippets = [text for text in (row['body'], detail.get('body')) if text]
    snippets.extend(text for key in ('titles', 'descriptions', 'captions') for text in extra.get(key, []))
    snippets.extend(item['observation'] for item in extra.get('media_observations', []))
    if extra.get('call_to_action'):
        snippets.append('行动按钮：' + extra['call_to_action'])
    if extra.get('destination'):
        snippets.append('观察到的目的地：' + extra['destination'])
    return ' · '.join(dict.fromkeys(snippets))[:2400] if snippets else None

def public_observations(workspace,account,path):
    if path is None:fail('PUBLIC_OBSERVATION_INPUT_REQUIRED')
    document = _value(path)
    if type(document) is dict and document.get('status') == 'PUBLIC_AD_QUERY_RETURNED':
        document = {key: document['observation'][key] for key in ('scope','records','coverage')}
    normalized=_normalize_public(document)
    ws=journal_for(workspace,existing_only=True)
    with ws.locked(existing_only=True):
        profile=load(ws)
        scope=draft_review_scope(profile,account,host_draft=True)
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
            keys = ('id', 'brief')
            object_only(variant, (*keys, 'copy', 'storyboard'), keys)
            names.add(_ref(variant['id']))
            _short(variant['brief'])
            if 'copy' in variant:
                from .meta_write_mapping import CREATIVE_CTAS

                copy = variant['copy']
                _fixed(copy, ('primary_text', 'headline', 'description', 'call_to_action'))
                for field, limit in (('primary_text', 2000), ('headline', 120), ('description', 500)):
                    _short(copy[field], limit)
                if copy['call_to_action'] not in CREATIVE_CTAS:
                    fail('RESEARCH_VARIANT_CALL_TO_ACTION')
            if 'storyboard' in variant:
                last_time = -1
                for beat in _observations(variant['storyboard'], 12):
                    _fixed(beat, ('start_ms', 'visual', 'script'))
                    offset = beat['start_ms']
                    if type(offset) is not int or not last_time < offset <= 600_000:
                        fail('RESEARCH_STORYBOARD_ORDER')
                    _short(beat['visual'], 500)
                    _short(beat['script'], 800)
                    last_time = offset
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
    return _frozen(state, concept_id, variant_id, asset_sha256)


def _frozen(state, concept_id, variant_id, asset_sha256, *, allow_used=False):
    concept = state['concepts'].get(_ref(concept_id))
    if type(asset_sha256) is not str or not SHA.fullmatch(asset_sha256):
        fail('RESEARCH_MATERIAL_HASH_REQUIRED')
    review = state['reviews'].get(asset_sha256)
    if (concept is None or (concept['state'] != 'AVAILABLE' and not allow_used) or review is None
            or review['concept_id'] != concept_id):
        fail('RESEARCH_PLAN_SELECTION_MISMATCH')
    variant = next((row for row in concept['variants'] if row['id'] == _ref(variant_id)), None)
    if variant is None:
        fail('RESEARCH_PLAN_VARIANT_MISMATCH')
    binding = dict(concept_id=concept_id, variant_id=variant_id, asset_sha256=asset_sha256,
                   review_sha256=digest(review), brief_sha256=digest(variant),
                   source_sha256=digest([state['sources'][key] for key in concept['source_ids']]))
    if 'copy' in variant:
        binding['creative_copy'] = variant['copy']
    return binding


def selection_from_file(journal, profile, account, path):
    packet = _value(path)
    _fixed(packet, ('schema', 'workspace_identity_sha256', 'account_id', 'selection'))
    if (packet['schema'] != 'creative-comparison-choice-v1' or packet['account_id'] != account
            or packet['workspace_identity_sha256'] != workspace_identity(profile)
            or type(packet['selection']) is not dict):
        fail('CREATIVE_COMPARE_CHOICE_SCOPE')
    selection = packet['selection']
    keys = ('concept_id', 'variant_id', 'asset_sha256')
    if not selection.keys() >= set(keys):
        fail('CREATIVE_COMPARE_CHOICE_SCOPE')
    args = tuple(selection[key] for key in keys)
    if freeze_selection(journal, profile, account, *args) != selection:
        fail('CREATIVE_COMPARE_CHOICE_STALE')
    return args


def import_public_sources(document, profile, account_id, product):
    _short(product, 180)
    if (document.get('account_id') != account_id
            or document.get('workspace_identity_sha256') != workspace_identity(profile)):
        fail('PUBLIC_OBSERVATION_WORKSPACE_SCOPE_MISMATCH')
    normalized = _normalize_public({key: document[key] for key in ('scope','records','coverage')})
    if digest(normalized) != document.get('evidence_sha256'):
        fail('PUBLIC_OBSERVATION_RECEIPT_MISMATCH')
    return [
        dict(id='LIB-' + digest([row['page_id'], row['ad_archive_id'], row['observed_at'], fact])[:40],
             kind='PUBLIC_OBSERVATION', url=row['source_url'], observed_at=row['observed_at'], product=product,
             fact=fact)
        for row in normalized['records'] if (fact := _usable_public_fact(row)) is not None
    ]


def run_research(workspace, account, path, *, public_path=None, product=None):
    if (public_path is None) != (product is None):
        fail('PUBLIC_OBSERVATION_PRODUCT_AND_RECEIPT_REQUIRED')
    update = _value(path) if path is not None else None
    raw_public = _value(public_path) if public_path is not None else None
    ws = journal_for(workspace, existing_only=True)
    with ws.locked(existing_only=True):
        profile = load(ws)
        account_scope = draft_review_scope(profile, account, host_draft=True)
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



def _comparison_frames(media_reports, known_assets):
    from hashlib import sha256
    from .local_paths import checked_path

    frames = {}
    for path in media_reports:
        path = checked_path(path, link_error='RESEARCH_PATH_LINK', volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
        report = _value(path)
        asset = report.get('media_sha256')
        if (report.get('status') not in ('LOCAL_MEDIA_EVIDENCE', 'LOCAL_MEDIA_PARTIAL')
                or type(asset) is not str or asset not in known_assets or asset in frames):
            fail('CREATIVE_COMPARE_MEDIA_SCOPE')
        samples = []
        for frame in _observations(report.get('frames'), 3):
            _fixed(frame, ('timestamp_ms', 'path', 'sha256'))
            name, timestamp = frame['path'], frame['timestamp_ms']
            if type(name) is not str or not re.fullmatch(r'frame-[0-9]{2}\.png', name):
                fail('CREATIVE_COMPARE_FRAME_PATH')
            if type(timestamp) is not int or timestamp < 0:
                fail('CREATIVE_COMPARE_FRAME_TIMESTAMP')
            source = checked_path(path.parent / name, link_error='RESEARCH_PATH_LINK',
                                  volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
            if not source.is_file() or source.stat().st_size > 2_000_000:
                fail('CREATIVE_COMPARE_FRAME_INVALID')
            with source.open('rb') as stream:
                data = stream.read(2_000_001)
            if (len(data) > 2_000_000 or not data.startswith(b'\x89PNG\r\n\x1a\n')
                    or sha256(data).hexdigest() != frame['sha256']):
                fail('CREATIVE_COMPARE_FRAME_INVALID')
            samples.append((timestamp, data))
        frames[asset] = samples
    return frames

def compare_concepts(workspace, account, concept_ids, output, *, media_reports=()):
    from html import escape
    from .meta_write_mapping import _write_new
    from .monitor_tools import new_directory

    chosen = [_ref(value) for value in concept_ids]
    if not 2 <= len(chosen) <= 10 or len(set(chosen)) != len(chosen):
        fail("CREATIVE_COMPARE_SELECTION")
    current = run_research(workspace, account, None)
    research = current["research"]
    rows = []
    for ref in chosen:
        concept = research["concepts"].get(ref)
        if concept is None:
            fail("CREATIVE_COMPARE_CONCEPT_NOT_FOUND")
        reviews = sorted((row for row in research["reviews"].values() if row["concept_id"] == ref),
                         key=lambda row: row["asset_sha256"])
        rows.append(dict(concept_id=ref, hypothesis=concept["hypothesis"], state=concept["state"],
                         evidence_refs=concept["source_ids"], variants=concept["variants"],
                         media=[dict(asset_sha256=row["asset_sha256"], comments=row["comments"])
                                for row in reviews]))
    known_assets = {media["asset_sha256"] for row in rows for media in row["media"]}
    if len(media_reports) > 12:
        fail('CREATIVE_COMPARE_MEDIA_LIMIT')
    frames = _comparison_frames(media_reports, known_assets)
    root = new_directory(output)
    for row in rows:
        for media in row["media"]:
            asset = media["asset_sha256"]
            samples = []
            for index, (timestamp, data) in enumerate(frames.get(asset, [])):
                filename = f'frame-{asset}-{index}.png'
                if not (root / filename).exists():
                    with (root / filename).open('xb') as stem:
                        stem.write(data)
                samples.append(dict(file=filename, timestamp_ms=timestamp))
            if media_reports:
                media["frames"] = samples

    result = dict(status="CREATIVE_CONCEPTS_COMPARED", account_id=research["account_id"],
                  source_sha256=current["source_sha256"], concepts=rows, network_used=False, write_authorized=False)
    for row in rows:
        for media in row['media']:
            choices = []
            if row['state'] == 'AVAILABLE':
                for variant in row['variants']:
                    selection = _frozen(research, row['concept_id'], variant['id'], media['asset_sha256'])
                    name = f'choice-{digest(selection)}.json'
                    _write_new(root, name, dict(schema='creative-comparison-choice-v1',
                        workspace_identity_sha256=research['workspace_identity_sha256'],
                        account_id=research['account_id'], selection=selection))
                    choices.append(dict(file=name, variant=variant['id']))
            media['choices'] = choices
    _write_new(root, "comparison.json", result)
    with (root / "comparison.html").open("x", encoding="utf-8") as page:
        page.write('<!doctype html><html lang="zh"><meta charset="utf-8">'
                   '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\'">'
                   '<title>创意概念对照</title><h1>创意概念对照</h1>')
        for row in rows:
            page.write(f'<section><h2>{escape(row["concept_id"])}</h2><p>{escape(row["hypothesis"])}</p><ul>')
            for variant in row["variants"]:
                page.write(f'<li><h3>{escape(variant["id"])}</h3><p>{escape(variant["brief"])}</p>')
                for value in variant.get('copy', {}).values():
                    page.write(f'<p>{escape(value)}</p>')
                for beat in variant.get('storyboard', []):
                    page.write(f'<p>{beat["start_ms"]} ms · {escape(beat["visual"])} · {escape(beat["script"])}</p>')
                page.write('</li>')
            page.write(f'</ul><p>素材评审 {len(row["media"])} 项</p>')
            for media in row["media"]:
                for sample in media.get("frames", []):
                    filename = escape(sample["file"])
                    timestamp = sample["timestamp_ms"]
                    page.write(f'<figure><img width="320" src="{filename}" alt="素材帧 {timestamp} ms">'
                               f'<figcaption>{timestamp} ms</figcaption></figure>')
                for axis, comment in media["comments"].items():
                    if comment["state"] == "OBSERVED":
                        page.write(f'<p>{escape(axis)}：{escape(comment["comment"])}</p>')
                for choice in media['choices']:
                    page.write(f'<p><a href="{choice["file"]}" download>保存 {escape(choice["variant"])} 的选择</a></p>')
            page.write('</section>')
    return result
