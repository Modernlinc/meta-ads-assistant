"""Compare fresh read evidence with a reviewed action; never dispatch a write.

ApprovalVerifier is a trusted program interface, not an input JSON boolean.
Its default implementation rejects every call. This module supplies no human
identity provider, approval issuer, write transport or approval persistence.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
import re
import time

from .creation import ApprovalVerifier
from .http_read import GraphReadTransport
from .journal import canonical, digest
from .meta_write_mapping import PROPOSAL_COMPARISON, PROPOSAL_PACKET_REQUIRED, utcnow, _write_new
from .read_adapter import GRAPH_AUTH_ERRORS
from .validation import InputError, aware_time, fail, object_only, strict_json
from .write_snapshots import (CapturedSnapshots, SyntheticSnapshotTransport, collect_snapshots, validate_bundle,
                              review_snapshots)

APPROVAL_KEYS=('schema','review_sha256','approval_reference','approved_at','expires_at')
READ_ERRORS=frozenset(('SNAPSHOT_CAPTURE_DEADLINE','SNAPSHOT_CHANGED_DURING_CAPTURE',
    'SNAPSHOT_READ_BINDING_CHANGED','SNAPSHOT_POST_READ_AUTHORIZATION_FAILED',
    *('SNAPSHOT_' + code for code in GRAPH_AUTH_ERRORS),'SNAPSHOT_PERMISSION','SNAPSHOT_RATE_LIMIT',
    'SNAPSHOT_OBJECT_OR_PARAMETER','SNAPSHOT_SERVICE_UNAVAILABLE',
    'SNAPSHOT_NOT_ALLOWLISTED','SNAPSHOT_PARENT_RELATION_MISMATCH',
    'SNAPSHOT_CREATIVE_PAGE_MISMATCH','SNAPSHOT_OBJECT_MISMATCH','SNAPSHOT_ACCOUNT_MISMATCH'))


def _json(value): return strict_json(canonical(value))


def _bundle(value):
    result=strict_json(value.payload) if type(value) is CapturedSnapshots else _json(value)
    if type(result) is not dict: fail('RECHECK_SNAPSHOT_OBJECT')
    aware_time(result.get('ended_at'))
    return result


def _refresh_draft(draft):
    value=_json(draft)
    # Only read-derived facts change. Business intent and the reviewed Page /
    # material references remain fixed, including operation, target and amount.
    for key in value['context']:
        if key not in ('page_id','material_sha256',PROPOSAL_COMPARISON): value['context'][key]=None
    value['before']={'configured_status':None,'daily_budget_minor':None}
    return value


def _differences(before, after, path=''):
    if before==after: return []
    if type(before) is dict and type(after) is dict:
        rows=[]
        for key in sorted(before.keys() | after.keys()):
            pointer=path+'/'+key
            if key not in before or key not in after:
                rows.append(dict(path=pointer,before_present=key in before,after_present=key in after,
                                 before=deepcopy(before.get(key)),after=deepcopy(after.get(key))))
            else: rows.extend(_differences(before[key],after[key],pointer))
        return rows
    return [dict(path=path,before_present=True,after_present=True,before=deepcopy(before),after=deepcopy(after))]


def compare_recheck(draft, before, after, expected_review_sha256, *, material_directory=None, clock=utcnow):
    """Diagnostic comparison. A match is neither human approval nor execution."""
    packet = None
    if material_directory is not None:
        from .material_bundle import load_material_packet
        packet = load_material_packet(material_directory,draft,clock=clock)
    return _compare_recheck(draft,before,after,expected_review_sha256,packet,clock)


def _with_material(review, packet):
    if packet is None:
        return review
    from .material_bundle import attach_material_packet
    return attach_material_packet(review,packet)


def _compare_recheck(draft, before, after, expected_review_sha256, packet, clock):
    draft=_json(draft); old=_bundle(before); new=_bundle(after)
    old_end=aware_time(old['ended_at'])
    baseline=_with_material(review_snapshots(draft,before,clock=lambda:old_end),packet)
    if expected_review_sha256!=baseline['review_sha256']: fail('RECHECK_REVIEW_CHANGED')
    if PROPOSAL_PACKET_REQUIRED in baseline['blockers']: fail(PROPOSAL_PACKET_REQUIRED)
    if baseline['missing_fields']: fail('RECHECK_REVIEW_INCOMPLETE')
    now=clock(); aware_time(now.isoformat())
    fresh_draft=_refresh_draft(draft)
    rows=validate_bundle(fresh_draft,new,clock=lambda:now)
    if aware_time(new['started_at']) < old_end: fail('RECHECK_NOT_AFTER_BASELINE')
    changes=_differences(dict(rows=baseline['snapshot_evidence']['rows'],allowlist=old['allowlist'],
                              read_identity=old['read_identity'],source_mode=old['source_mode']),
                         dict(rows=rows,allowlist=new['allowlist'],read_identity=new['read_identity'],source_mode=new['source_mode']))
    current=None; profile_error=None
    try:
        current=_with_material(review_snapshots(fresh_draft,after,clock=lambda:now),packet)
    except InputError:
        # Snapshot validation already passed. Remaining failure is a mapping /
        # business profile constraint, never proof of a ready write.
        current=None
        profile_error='RECHECK_CURRENT_PROFILE_UNSUPPORTED'
    decision='REAPPROVAL_REQUIRED' if changes else 'RECHECK_MATCHED_EXECUTION_BLOCKED'
    if profile_error is not None: decision='REAPPROVAL_REQUIRED'
    if packet is not None and packet[1]: decision='REAPPROVAL_REQUIRED'
    binding=dict(review_sha256=baseline['review_sha256'],draft_sha256=digest(draft),
                 before_bundle_sha256=old['bundle_sha256'],after_bundle_sha256=new['bundle_sha256'],
                 before_state_sha256=baseline['snapshot_evidence']['state_sha256'],after_state_sha256=digest(rows),
                 current_review_sha256=current['review_sha256'] if current else None,
                 changes=changes,decision=decision,profile_error=profile_error,checked_at=now.isoformat())
    if packet is not None:
        binding['material_recheck_findings'] = packet[1]
    return dict(schema='meta-write-recheck-v1',status=decision,binding=binding,recheck_sha256=digest(binding),
                baseline_review=baseline,current_review=current,
                declared_current_snapshot_mode=new['source_mode'],
                current_snapshot_provenance=current['snapshot_evidence']['provenance'] if current else 'UNUSABLE_FOR_REVIEW',
                dispatchable=False,human_identity_verified=False,write_authorized=False,
                approval_ledger_updated=False,approval_invalidated_remotely=False,
                limitation='FIXED_FIELDS_ONLY_NOT_ATOMIC_NOT_FULL_MATERIAL_OR_RIGHTS_REVIEW')


def validate_approval_reference(value, review_sha256, baseline_end):
    object_only(value,APPROVAL_KEYS,APPROVAL_KEYS)
    if value['schema']!='write-recheck-approval-reference-v1': fail('RECHECK_APPROVAL_SCHEMA')
    if (type(value['review_sha256']) is not str or not re.fullmatch('[a-f0-9]{64}',value['review_sha256'])
            or value['review_sha256']!=review_sha256): fail('RECHECK_APPROVAL_BINDING')
    if type(value['approval_reference']) is not str or not re.fullmatch('APPROVAL_[A-Z0-9_]{1,64}',value['approval_reference']):
        fail('RECHECK_APPROVAL_REFERENCE')
    start=aware_time(value['approved_at']); end=aware_time(value['expires_at'])
    if not baseline_end <= start < end or end-start>timedelta(minutes=15) or start-baseline_end>timedelta(minutes=5):
        fail('RECHECK_APPROVAL_WINDOW')
    return start,end


def collect_approved_recheck(draft, before, approval_reference, transport_factory, *, verifier=None,
                             clock=utcnow,monotonic=time.monotonic,before_request=None,synthetic_material_evidence=None):
    """Program integration seam. Callers must separately authorize the GET scope.

    Only an in-process baseline may enter this path; serialized files are
    diagnostic inputs only. A trusted verifier must check its actual approval
    ledger, revocation, identity and one-time action binding on EVERY verify.
    No result is a reusable permit. An executor still needs a final verifier /
    state check, durable UNKNOWN, independent write/readback and full profiles.
    """
    draft=_json(draft); approval=_json(approval_reference); old=_bundle(before)
    baseline=review_snapshots(draft,before,clock=lambda:aware_time(old['ended_at']))
    if baseline['snapshot_evidence']['provenance']=='IMPORTED_UNATTESTED': fail('RECHECK_BASELINE_UNATTESTED')
    packet = None
    if synthetic_material_evidence is not None:
        if baseline['snapshot_evidence']['provenance']!='IN_PROCESS_SYNTHETIC':
            fail('RECHECK_SYNTHETIC_MATERIAL_EVIDENCE_ONLY')
        packet=(_json(synthetic_material_evidence),[])
        baseline=_with_material(baseline,packet)
    if PROPOSAL_PACKET_REQUIRED in baseline['blockers']: fail(PROPOSAL_PACKET_REQUIRED)
    if baseline['missing_fields']: fail('RECHECK_REVIEW_INCOMPLETE')
    approved,expires=validate_approval_reference(approval,baseline['review_sha256'],aware_time(old['ended_at']))
    if approved-aware_time(old['started_at'])>timedelta(minutes=5): fail('RECHECK_BASELINE_TOO_OLD')
    verifier=ApprovalVerifier() if verifier is None else verifier
    if not isinstance(verifier,ApprovalVerifier): fail('RECHECK_VERIFIER_INTERFACE')
    start=clock(); tick=monotonic(); previous=start; previous_tick=tick

    def window():
        nonlocal previous,previous_tick
        now=clock(); current=monotonic(); aware_time(now.isoformat())
        if (not approved<=now<expires or now<previous or not previous_tick<=current<=tick+60
                or now-start>timedelta(seconds=60)): fail('RECHECK_APPROVAL_EXPIRED_OR_CLOCK_CHANGED')
        previous=now; previous_tick=current
        return now

    def check():
        now=window()
        try:
            if verifier.verify(deepcopy(baseline),deepcopy(approval),now.isoformat()) is not None:
                fail('RECHECK_APPROVAL_VERIFICATION_FAILED')
        except Exception: fail('RECHECK_APPROVAL_VERIFICATION_FAILED')
        window()  # Provider IO itself can outlive approval or the read window.

    check()  # Default verifier fails before constructing any transport.
    def reserve(plan):
        check()
        if before_request is not None: before_request(plan)
        check()  # Audit callback may take time or the approval may be revoked.

    def scoped_factory(plan):
        transport=transport_factory(plan)
        expected=GraphReadTransport if old['source_mode']=='GRAPH_READ' else SyntheticSnapshotTransport
        if type(transport) is not expected: fail('RECHECK_READ_SOURCE_CHANGED')
        if expected is GraphReadTransport and dict(app_id=transport.binding.app_id,
                principal_id=transport.binding.principal_id)!=old['read_identity']:
            fail('RECHECK_READ_IDENTITY_CHANGED')
        return transport

    try:
        fresh=collect_snapshots(_refresh_draft(draft),old['allowlist'],scoped_factory,
                                clock=clock,monotonic=monotonic,before_request=reserve,
                                synthetic_creative=old['source_mode']=='SYNTHETIC')
    except Exception as exc:
        if type(exc) is InputError and str(exc) in READ_ERRORS | {
            'RECHECK_APPROVAL_VERIFICATION_FAILED','RECHECK_APPROVAL_EXPIRED_OR_CLOCK_CHANGED',
            'RECHECK_READ_SOURCE_CHANGED','RECHECK_READ_IDENTITY_CHANGED'}: raise
        fail('RECHECK_READ_FAILED')
    check(); new=_bundle(fresh)
    if aware_time(new['started_at'])<approved: fail('RECHECK_READ_PRECEDES_APPROVAL')
    if packet is None:
        report=compare_recheck(draft,before,fresh,baseline['review_sha256'],clock=clock)
    else:
        report=_compare_recheck(draft,before,fresh,baseline['review_sha256'],packet,clock)
    check()
    report.update(approval_reference=approval,approval_provider_checks_completed=True,
                  get_attempts=new['request_count'],scope='ONE_RECHECK_ONLY_NOT_EXECUTION_OR_NEW_READ_AUTHORIZATION')
    return report


def export_recheck(draft,before,after,expected_review_sha256,output=None,*,material_directory=None,clock=utcnow):
    report=compare_recheck(draft,before,after,expected_review_sha256,material_directory=material_directory,clock=clock)
    if output is not None:
        from .monitor_tools import new_directory
        root=new_directory(output); files=[_write_new(root,'RECHECK.json',report)]
        text=('# 写入前快照复查\n\n状态：'+report['status']+'\n\n仅比较固定字段，不授予批准或执行。'
              '文件来源未验真；变化时需要重新提案、审阅与批准。审批账本没有被本报告修改。\n\n'
              '```json\n'+json.dumps(report['binding'],ensure_ascii=False,indent=2)+'\n```\n')
        raw=text.encode('utf-8')
        with (root/'RECHECK.md').open('xb') as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        files.append(dict(path='RECHECK.md',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
        _write_new(root,'RECEIPT.json',dict(status=report['status'],files=files,dispatchable=False,real_meta_requests=0))
    return report


def approval_reference_schema():
    props=dict(schema={'const':'write-recheck-approval-reference-v1'},
        review_sha256={'type':'string','pattern':'^[a-f0-9]{64}$'},
        approval_reference={'type':'string','pattern':'^APPROVAL_[A-Z0-9_]{1,64}$'},
        approved_at={'type':'string','format':'date-time'},expires_at={'type':'string','format':'date-time'})
    return {'$schema':'https://json-schema.org/draft/2020-12/schema','type':'object','additionalProperties':False,
        'properties':props,'required':list(props),'title':'Non-secret external approval reference; not a grant',
        '$comment':'Runtime binds the exact reviewed request/Diff/snapshot hash, evidence <=5 minutes at approval, approval <=15 minutes and current time. A trusted ApprovalVerifier must consult its actual identity, revocation and one-time action ledger at every checkpoint. Default verifier always refuses. JSON fields or a matching hash never verify a human or authorize reads/writes. No issuer or durable write executor is provided here.'}
