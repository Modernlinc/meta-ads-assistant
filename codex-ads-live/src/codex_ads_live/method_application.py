"""Bind assistant-authored method outputs to actual, local Meta workflow consumers."""

from pathlib import Path

from . import batch_knowledge
from .business_profile import SECRET_PATTERN
from .journal import digest
from .meta_write_mapping import _write_new
from .method_handoff import _source_identity, build_receipt
from .method_workflow import read_plan
from .monitor_tools import new_directory
from .operator_review import evidence_text, load_input
from .validation import fail, object_only

_REVIEWS = {
    'strategy-review', 'business-readiness', 'performance-diagnose', 'tracking-audit',
}


def _details(value, *, required=False):
    if type(value) is not list or len(value) > 12 or (required and not value):
        fail('METHOD_APPLICATION_EVIDENCE_LIST')
    return [evidence_text(text) for text in value]


def _source_file(name, materials):
    if type(name) is not str:
        fail('METHOD_APPLICATION_SOURCE_NOT_DECLARED')
    current = _source_identity(name)
    if current not in materials:
        fail('METHOD_APPLICATION_SOURCE_NOT_DECLARED')
    return Path(current['path'])


def _run_consumer(spec, workspace, account, material_paths, output, receipt_files, briefs, knowledge):
    kind = spec['command']
    if kind in _REVIEWS:
        from . import operator_review
        object_only(spec, ('command','document'), ('command','document'))
        document = spec['document']
        if type(document) is not dict:
            fail('METHOD_CONSUMER_INPUT')
        scoped = [document]
        if kind == 'performance-diagnose':
            scoped = [document.get(side) for side in ('before','after')]
        if (account is not None and kind != 'tracking-audit'
                and any(type(row) is not dict or row.get('account_id') != account for row in scoped)):
            fail('METHOD_CONSUMER_ACCOUNT_MISMATCH')
        return getattr(operator_review, kind.replace('-', '_'))(document)
    if kind == 'public-ad-inspect':
        from .ads_library import inspect_detail
        fields = ('command','observation','page_id','archive_id')
        object_only(spec, (*fields,'media_report','hypothesis_ref'), (*fields,'hypothesis_ref'))
        matches = [row['text'] for brief in briefs for row in brief['results']
                   if row['ref'] == spec['hypothesis_ref'] and row['kind'] == 'objection_hypothesis'
                   and row['state'] == 'HYPOTHESIS']
        if len(matches) != 1:
            fail('METHOD_CONSUMER_HYPOTHESIS_REQUIRED')
        media = _source_file(spec['media_report'], material_paths) if 'media_report' in spec else None
        return inspect_detail(_source_file(spec['observation'], material_paths),
                              spec['page_id'], spec['archive_id'], media, matches[0])
    if kind == 'commerce-reconcile':
        from .commerce_csv import reconcile
        from .commerce_report import export
        fields = ('command','orders','refunds','spend','store_id','account_id','currency','since','until','refund_basis')
        object_only(spec, fields, fields)
        if account is not None and spec['account_id'] != account:
            fail('METHOD_CONSUMER_ACCOUNT_MISMATCH')
        report = reconcile(*(_source_file(spec[key], material_paths) for key in ('orders','refunds','spend')),
                           *(spec[key] for key in ('store_id','account_id','currency','since','until','refund_basis')))
        return dict(report, report_files=export(report, output/'commerce'))
    if account is None:
        fail('METHOD_CONSUMER_ACCOUNT_REQUIRED')
    if kind == 'creative-compare':
        from .creative_research import compare_concepts
        fields = ('command','concept_ids')
        object_only(spec, (*fields,'media_reports'), fields)
        references = spec.get('media_reports', [])
        if type(references) is not list or len(references) > 6:
            fail('METHOD_CONSUMER_INPUT')
        media = [_source_file(path, material_paths) for path in references]
        return compare_concepts(workspace, account, spec['concept_ids'], output/'creative', media_reports=media)
    if kind == 'media-buyer prepare':
        from .media_buyer import run_case
        fields = ('command','case','intake')
        object_only(spec, fields, fields)
        doc = spec['intake']
        business = doc.get('business') if type(doc) is dict else None
        if type(business) is not dict or 'method_receipts' in doc or business.get('account_id') != account:
            fail('METHOD_CONSUMER_ACCOUNT_OR_RECEIPT_CONFLICT')
        partner = doc.get('partner_briefs', [])
        if type(partner) is not list:
            fail('METHOD_CONSUMER_INPUT')
        if knowledge:
            sample = briefs[0]
            observed_at = sample['results'][0]['observed_at']
            partner = [*partner, *batch_knowledge.briefs(knowledge, sample['product'], sample['market'], observed_at)]
        input_path = output/'CASE-INTAKE.json'
        _write_new(output, input_path.name, dict(doc, partner_briefs=partner, method_receipts=list(map(str, receipt_files))))
        return run_case(workspace, spec['case'], 'prepare', input_path)
    fail('METHOD_CONSUMER_UNAVAILABLE')


def apply(directory, plan, intent, host_input, output, source_materials):
    scope, route, ready, plan_digest = read_plan(plan, directory, intent)
    proposal = load_input(host_input)
    fields = ('schema','methods','consumer')
    object_only(proposal, (*fields, 'knowledge'), fields)
    if proposal['schema'] != 'meta-method-application-v1':
        fail('METHOD_APPLICATION_SCHEMA')
    spec = proposal['consumer']
    kind = spec.get('command') if type(spec) is dict else None
    if kind not in route['downstream_consumers']:
        fail('METHOD_CONSUMER_OUTSIDE_INTENT')
    if not 1 <= len(source_materials) <= 12:
        fail('METHOD_HANDOFF_SOURCE_MATERIAL_REQUIRED')
    materials = [_source_identity(path) for path in source_materials]
    if len(materials) != len({row['path'] for row in materials}):
        fail('METHOD_APPLICATION_DUPLICATE_SOURCE')
    knowledge = batch_knowledge.applied(proposal.get('knowledge', []), intent, kind, materials)
    submissions = proposal['methods']
    if type(submissions) is not dict or submissions.keys() != ready.keys():
        fail('METHOD_APPLICATION_RESULT_PER_READY_METHOD_REQUIRED')

    receipt_data, briefs = {}, []
    for name, method in ready.items():
        submitted = submissions[name]
        fields = ('deliverable','brief','read_files','input_requirements','assumptions','unknowns')
        object_only(submitted, fields, fields)
        files = submitted['read_files']
        # The assistant attests what it read. The program independently checks
        # identities and hashes, without claiming to observe its reasoning.
        required = {method['entry'], 'method.md'} if name in route['core'] else {method['entry']}
        if type(files) is not list or any(type(item) is not str for item in files):
            fail('METHOD_APPLICATION_READING_REQUIRED')
        observed = set(files)
        if not required <= observed or len(observed) != len(files):
            fail('METHOD_APPLICATION_READING_REQUIRED')
        if not observed <= set(method['read_files']):
            fail('METHOD_APPLICATION_UNVERIFIED_REFERENCE')
        if any('/references/' in f'/'+path for path in method['read_files']) and len(files)==1:
            fail('METHOD_APPLICATION_REFERENCE_REQUIRED')
        details = dict(input_requirements=_details(submitted['input_requirements'], required=True),
                       assumptions=_details(submitted['assumptions']), unknowns=_details(submitted['unknowns']))
        application = dict(intent=intent, plan_sha256=plan_digest, consumer=kind, consumer_input_sha256=digest(spec),
                           method_documents=files, **details)
        result = dict(deliverable=submitted['deliverable'], brief=submitted['brief'], application=application)
        receipt, brief = build_receipt(result, name, method,
            dict(workspace_identity=scope['workspace_sha256'], account_id=scope['account_id']), materials)
        receipt_data[name] = receipt
        briefs.append(brief)

    root = new_directory(output)
    receipts = []
    for name, data in receipt_data.items():
        folder = new_directory(root/name)
        _write_new(folder, 'METHOD-RECEIPT.json', data)
        receipts.append(folder/'METHOD-RECEIPT.json')
    consumer_intake = _write_new(root, 'CONSUMER-INPUT.json', spec)
    result = _run_consumer(spec, directory, scope['account_id'], materials, root, receipts, briefs, knowledge)
    if SECRET_PATTERN.search(str(result)):
        fail('METHOD_CONSUMER_SENSITIVE_OUTPUT')
    artifact = _write_new(root, 'BUSINESS-RESULT.json', result)
    knowledge_files = {
        name: _write_new(root, name+'-KNOWLEDGE-RECEIPT.json',
                         dict(record, consumer_input_sha256=digest(spec), downstream_result=artifact))
        for name,record in knowledge.items()
    }
    report = dict(status='METHOD_OUTPUT_CONSUMED_LOCALLY', knowledge_receipts=knowledge_files, action_authorized=False, network_used=False, intent=intent,
                  plan_sha256=plan_digest, consumer=kind, consumer_status=result['status'],
                  consumer_input_sha256=digest(spec), consumer_input=consumer_intake, result=artifact, method_receipts=[
                      dict(path=path.relative_to(root).as_posix(), payload_sha256=data['payload_sha256'])
                      for path,data in zip(receipts,receipt_data.values(),strict=True)])
    _write_new(root, 'CONSUMPTION.json', report)
    return report
