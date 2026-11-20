"""Reopen a completed local material export for parameter review."""
import hashlib
import re

from .journal import digest
from .local_paths import checked_path
from .materials import check_media_files, exported_media_path, material_findings, validate_material
from .meta_write_mapping import PROPOSAL_PACKET_REQUIRED, require_proposal_comparison, validate_draft
from .qualified_leads import PATTERNS
from .validation import aware_time, fail, object_only, strict_json

READY = 'MATERIAL_REVIEW_READY_EXECUTION_BLOCKED'
HASH = re.compile('[0-9a-f]{64}')


def _read(root, name, limit):
    path = checked_path(root/name,link_error='MATERIAL_PACKET_PATH_LINK',volume_error='MATERIAL_VOLUME_NOT_MOUNTED')
    if not path.is_file(): fail('MATERIAL_PACKET_FILE_REQUIRED')
    with path.open('rb') as handle:
        raw = handle.read(limit+1)
    if len(raw)>limit: fail('MATERIAL_PACKET_FILE_TOO_LARGE')
    return raw


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _listed(root, entries, name, limit):
    raw = _read(root,name,limit)
    if entries.get(name) != dict(path=name,bytes=len(raw),sha256=_sha(raw)):
        fail('MATERIAL_PACKET_FILE_CHANGED')
    return raw


def _hash_string(value):
    if type(value) is not str or not HASH.fullmatch(value):
        fail('MATERIAL_PACKET_PROPOSAL_BINDING')
    return value


def _proposal(root, entries, receipt, report, draft):
    binding = report.get('proposal_binding')
    if type(binding) is not dict: fail('MATERIAL_PACKET_PROPOSAL_BINDING')
    payload = strict_json(_listed(root,entries,'proposal-context.json',64_000).decode('utf-8'))
    if (type(payload) is not dict or payload.get('schema')!='workspace-draft-review-context-v1'
            or payload.get('dispatchable') is not False or payload.get('remote_write_authorized') is not False):
        fail('MATERIAL_PACKET_PROPOSAL_BINDING')
    basis = payload.get('proposal_basis')
    if (type(basis) is not dict or basis.get('account_id') != draft['account_id']
            or basis.get('decision') not in ('DEFER_CHANGE','INVESTIGATE_COST','REVIEW_ONE_TEST_PROPOSAL')):
        fail('MATERIAL_PACKET_PROPOSAL_BINDING')
    scope = {key:basis.get(key) for key in ('currency','timezone')}
    if (any(type(value) is not str or not re.fullmatch(PATTERNS[key],value) for key,value in scope.items())
            or any(draft['context'][key] not in (None,value) for key,value in scope.items())):
        fail('MATERIAL_PACKET_PROPOSAL_SCOPE')
    comparison = _hash_string(payload.get('comparison_sha256'))
    expected = dict(account_id=draft['account_id'],comparison_sha256=comparison,
                    proposal_context_sha256=digest(payload),material_sha256=report['material_sha256'],
                    bound_draft_sha256=digest(draft),proposal_decision=basis['decision'])
    stamp = digest(expected)
    if binding!=dict(expected,binding_sha256=stamp) or receipt.get('proposal_binding_sha256')!=stamp:
        fail('MATERIAL_PACKET_PROPOSAL_BINDING')
    evidence = dict(proposal_binding_sha256=stamp,comparison_sha256=comparison,proposal_scope=scope)
    if 'input_runs' in payload or 'input_runs_verification' in payload:
        from .draft_evidence import RUN_VERIFICATIONS, validate_comparison_input_runs
        validate_comparison_input_runs(payload.get('input_runs'))
        verification = payload.get('input_runs_verification')
        if verification not in RUN_VERIFICATIONS:
            fail('MATERIAL_PACKET_PROPOSAL_BINDING')
        evidence['proposal_run_verification'] = verification
    return evidence


def load_material_packet(directory,draft,*,clock):
    validate_draft(draft)
    if draft['operation']!='CREATE_AD' or draft['context']['material_sha256'] is None:
        fail('MATERIAL_PACKET_BOUND_AD_REQUIRED')
    root = checked_path(directory,link_error='MATERIAL_PACKET_PATH_LINK',volume_error='MATERIAL_VOLUME_NOT_MOUNTED')
    raw_receipt = _read(root,'RECEIPT.json',32_000)
    receipt = strict_json(raw_receipt.decode('utf-8'))
    if (type(receipt) is not dict or receipt.get('status')!=READY or receipt.get('dispatchable') is not False
            or type(receipt.get('files')) is not list or len(receipt['files'])>34):
        fail('MATERIAL_PACKET_COMPLETED_EXPORT_REQUIRED')
    entries = {}
    for row in receipt['files']:
        object_only(row,('path','bytes','sha256'),('path','bytes','sha256'))
        if type(row['path']) is not str or row['path'] in entries: fail('MATERIAL_PACKET_RECEIPT')
        entries[row['path']] = row
    saved = validate_draft(strict_json(_listed(root,entries,'draft.json',16_000).decode('utf-8')))
    if saved != draft: fail('MATERIAL_PACKET_DRAFT_MISMATCH')
    raw_report = _listed(root,entries,'MATERIAL_REVIEW.json',128_000)
    report = strict_json(raw_report.decode('utf-8'))
    if (type(report) is not dict or report.get('schema')!='ad-material-review-report-v1'
            or report.get('status')!=READY or report.get('dispatchable') is not False):
        fail('MATERIAL_PACKET_COMPLETED_EXPORT_REQUIRED')
    material = validate_material(report.get('material'))
    material_sha = digest(material)
    creative = draft['parameters']['creative']
    if (report.get('material_sha256')!=material_sha or draft['context']['material_sha256']!=material_sha
            or creative is None
            or (material['account_id'],material['page_id'],material['creative_id'])
            != (draft['account_id'],draft['context']['page_id'],creative['creative_id'])):
        fail('MATERIAL_PACKET_SCOPE_MISMATCH')
    names = {'draft.json','MATERIAL_REVIEW.json','MATERIAL_REVIEW.md'}
    for media in material['media']:
        path = exported_media_path(media)
        names.add(path)
        if entries.get(path)!=dict(path=path,bytes=media['bytes'],sha256=media['sha256']):
            fail('MATERIAL_PACKET_RECEIPT')
    proposal = {}
    if 'proposal_binding' in report or 'proposal_binding_sha256' in receipt or 'proposal-context.json' in entries:
        names.add('proposal-context.json')
        proposal = _proposal(root,entries,receipt,report,draft)
    require_proposal_comparison(draft,proposal.get('comparison_sha256'))
    if entries.keys()!=names: fail('MATERIAL_PACKET_RECEIPT')
    _listed(root,entries,'MATERIAL_REVIEW.md',256_000)
    checked = check_media_files(material,root,exported=True)
    reviewed_at = aware_time(report.get('reviewed_at'))
    now = clock()
    if reviewed_at>now or material_findings(material,clock=lambda:reviewed_at)[1]:
        fail('MATERIAL_PACKET_ARCHIVED_REVIEW_CONFLICT')
    evidence = dict(verification='LOCAL_ARCHIVE_AND_COPIED_BYTES',receipt_sha256=_sha(raw_receipt),
                    material_review_sha256=_sha(raw_report),material_sha256=material_sha,
                    bound_draft_sha256=digest(draft),reviewed_at=report['reviewed_at'],
                    copied_media=checked,source_refetched=False,rights_verified=False,**proposal)
    return evidence, material_findings(material,clock=lambda:now)[1]


def validate_stored_material_evidence(evidence, draft, material):
    """Validate the deterministic projection saved by the synthetic ledger."""
    fields = ('verification','receipt_sha256','material_review_sha256','material_sha256',
              'bound_draft_sha256','reviewed_at','copied_media','source_refetched','rights_verified')
    proposal = ('proposal_binding_sha256','comparison_sha256','proposal_scope')
    object_only(evidence,fields+proposal+('proposal_run_verification',),fields)
    if (evidence['verification']!='LOCAL_ARCHIVE_AND_COPIED_BYTES'
            or evidence['source_refetched'] is not False or evidence['rights_verified'] is not False
            or evidence['material_sha256']!=digest(material) or evidence['bound_draft_sha256']!=digest(draft)):
        fail('MATERIAL_PACKET_STORED_EVIDENCE_MISMATCH')
    for key in ('receipt_sha256','material_review_sha256'):
        _hash_string(evidence[key])
    expected = [{key:media[key] for key in ('asset_ref','sha256','bytes')} for media in material['media']]
    if evidence['copied_media'] != expected:
        fail('MATERIAL_PACKET_STORED_EVIDENCE_MISMATCH')
    archived_at = aware_time(evidence['reviewed_at'])
    if material_findings(material,clock=lambda:archived_at)[1]:
        fail('MATERIAL_PACKET_ARCHIVED_REVIEW_CONFLICT')
    if any(key in evidence for key in proposal):
        if not all(key in evidence for key in proposal):
            fail('MATERIAL_PACKET_PROPOSAL_BINDING')
        _hash_string(evidence['proposal_binding_sha256'])
        _hash_string(evidence['comparison_sha256'])
        scope = evidence['proposal_scope']
        object_only(scope,('currency','timezone'),('currency','timezone'))
        if any(type(value) is not str or not re.fullmatch(PATTERNS[key],value) for key,value in scope.items()):
            fail('MATERIAL_PACKET_PROPOSAL_SCOPE')
    if 'proposal_run_verification' in evidence:
        from .draft_evidence import RUN_VERIFICATIONS
        if ('proposal_binding_sha256' not in evidence or evidence['proposal_run_verification'] not in RUN_VERIFICATIONS):
            fail('MATERIAL_PACKET_PROPOSAL_BINDING')
    require_proposal_comparison(draft,evidence.get('comparison_sha256'))


def attach_material_packet(review, packet):
    evidence,findings = packet
    context = review['draft']['context']
    if any(context[key] not in (None,value) for key,value in evidence.get('proposal_scope',{}).items()):
        fail('MATERIAL_PACKET_MAPPED_SCOPE_CHANGED')
    require_proposal_comparison(review['draft'],evidence.get('comparison_sha256'))
    if PROPOSAL_PACKET_REQUIRED in review['blockers']:
        review['blockers'].remove(PROPOSAL_PACKET_REQUIRED)
    review['material_evidence'] = evidence
    review['current_material_findings'] = findings
    if findings:
        review['blockers'].append('MATERIAL_PACKET_REVIEW_REQUIRED')
    review['approval_binding']['material_evidence'] = evidence
    review['review_sha256'] = digest(review['approval_binding'])
    return review
