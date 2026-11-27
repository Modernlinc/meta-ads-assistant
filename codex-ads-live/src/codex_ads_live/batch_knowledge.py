"""Read the verified Batch 01 Meta references and record scoped applications."""

from hashlib import sha256
from pathlib import Path

from .method_bundle import _safe_file
from .operator_review import evidence_text
from .validation import fail, object_only, strict_json

_ROOT = Path(__file__).resolve().parents[2] / 'references/knowledge-batch01'
_MODES = ('COMPARED', 'USED_AS_BASIS', 'INAPPLICABLE_RETAINED')


def _read(relative, expected=None):
    path = _safe_file(_ROOT, Path(relative))
    if not path.is_file() or path.stat().st_size > 180_000:
        fail('BATCH_KNOWLEDGE_FILE_INVALID')
    raw = path.read_bytes()
    if expected is not None and sha256(raw).hexdigest() != expected:
        fail('BATCH_KNOWLEDGE_CONTENT_CHANGED')
    return raw.decode('utf-8')


def _catalog():
    value = strict_json(_read('catalog.json'))
    if value.get('schema') != 'batch01-skill-knowledge-v1':
        fail('BATCH_KNOWLEDGE_SCHEMA')
    return value


def candidates(intent):
    current = _catalog()
    return [dict(method_id=card['method_id'], title=card['title'], version=card['version'],
                 sha256=card['sha256'], entry=card['entry'], applicability=card['applicability'],
                 disposition=card['disposition'])
            for card in current['cards'].values() if intent in card['intents']]


def search(topic, query=None, *, method_id=None, offset=0, limit=50):
    current = _catalog()
    if method_id is not None:
        card = current['cards'].get(method_id)
        if card is None:
            fail('BATCH_KNOWLEDGE_METHOD_NOT_FOUND')
        sources = strict_json(_read('sources.json', current['sources_sha256']))['sources']
        return dict(status='BATCH01_METHOD_BODY_VERIFIED', method=card,
                    body=_read(card['entry'], card['sha256']),
                    sources={key:sources[key] for key in card['source_ids']},
                    original_bundle_sha256=current['original_zip_sha256'], actions_authorized=False)
    topics = {
        'strategy': ('strategy','automation'), 'creative': ('creative',), 'tracking': ('tracking',),
        'diagnosis': ('diagnosis','tracking'), 'commerce': ('commerce','diagnosis'), 'competitors': ('creative',),
    }
    if topic not in (*topics, 'all') or not isinstance(offset, int) or not 0 <= offset <= 1000 or not 1 <= limit <= 100:
        fail('BATCH_KNOWLEDGE_SEARCH_RANGE')
    if query is not None:
        query = evidence_text(query).casefold()
    rows = []
    for row in current['cards'].values():
        if topic != 'all' and row['domain'] not in topics[topic]:
            continue
        searchable = ' '.join([row['method_id'],row['title'],row['creator'],row['applicability'],row['limitations']])
        if not query or query in searchable.casefold():
            rows.append(row)
    return dict(status='BATCH01_KNOWLEDGE_CANDIDATES', count=len(rows), rows=rows[offset:offset+limit],
                actions_authorized=False)


def applied(submissions, intent, consumer, materials):
    if type(submissions) is not list or len(submissions) > 12:
        fail('BATCH_KNOWLEDGE_APPLICATION_COUNT')
    allowed = {row['method_id'] for row in candidates(intent)}
    fields = ('method_id','method_sha256','selection','applicability_check','contribution',
              'counterevidence','assumptions','unknowns')
    receipts = {}
    for row in submissions:
        object_only(row, fields, fields)
        name = row['method_id']
        if type(name) is not str or name not in allowed or name in receipts or row['selection'] not in _MODES:
            fail('BATCH_KNOWLEDGE_APPLICATION_SCOPE')
        looked_up = search('all', method_id=name)
        method = looked_up['method']
        if row['method_sha256'] != method['sha256']:
            fail('BATCH_KNOWLEDGE_CONTENT_CHANGED')
        if row['selection']=='USED_AS_BASIS' and method['disposition']=='RETRIEVABLE_REFERENCE':
            fail('BATCH_KNOWLEDGE_REFERENCE_CANNOT_COMPILE')
        for key in ('applicability_check','contribution','counterevidence'):
            evidence_text(row[key])
        for key in ('assumptions','unknowns'):
            if type(row[key]) is not list or len(row[key]) > 12:
                fail('BATCH_KNOWLEDGE_APPLICATION_EVIDENCE')
            for item in row[key]:
                evidence_text(item)
        receipts[name] = dict(application=row, consumer=consumer, method=method,
                              source_metadata=looked_up['sources'], source_materials=materials,
                              provenance='HOST_REPORTED_APPLICATION_WITH_VERIFIED_CARD_AND_SOURCES',
                              actions_authorized=False)
    return receipts


def briefs(applications, product, market, observed_at):
    return [dict(schema='partner-evidence-brief-v1', role=row['method']['role'], product=product, market=market,
                 items=[dict(kind=row['method']['brief_kind'], ref='EV_'+name.replace('-','_'),
                             text=row['application']['contribution'], observed_at=observed_at, state='HYPOTHESIS')])
            for name,row in applications.items() if row['application']['selection']!='INAPPLICABLE_RETAINED']
