"""A reproducible synthetic walkthrough, not a recommendation or human approval."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path

from .analysis import aggregate_ads
from .approval_ledger import DurableApprovalSimulation
from .creation import DurablePausedCreation, MockCreationTransport, SimulatedApprovalVerifier, proposal as creation_proposal
from .journal import canonical, digest
from .local_paths import checked_path
from .method_bundle import METHODS, bundled_route
from .read_adapter import ReadPlan, ReadSession, MockTransport
from .runtime import LocalRuntime
from .validation import fail
from .workflow import GATES, gate, validate_business_handoff
from .workflow_state import SyntheticWorkflow


def run_demo(directory):
    root = checked_path(directory, link_error='DEMO_PATH_SYMLINK', volume_error='DEMO_VOLUME_NOT_MOUNTED')
    if root.exists():
        fail('DEMO_REQUIRES_NEW_DIRECTORY')
    routes = {intent: bundled_route(intent) for intent in METHODS}
    if any(not route['status'].startswith('BUNDLED_') for route in routes.values()):
        fail('DEMO_METHOD_SOURCE_UNAVAILABLE')
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    artifacts = []

    def save(name, value):
        data = (canonical(value) + '\n').encode('utf-8')
        with (root / name).open('xb') as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        artifacts.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})

    save('01-methods.json', routes)
    workflow = SyntheticWorkflow(root / 'workflow-state')

    def verify_synthetic_stage(name):
        workflow.set_evidence(name, {key: True for key in GATES[name]})
        revision = workflow.begin(name)
        workflow.finish(name, revision, 'VERIFIED', ['SIM_WALKTHROUGH_RESULT'])

    for name in ('onboarding', 'asset_inventory', 'app_setup', 'connection'):
        verify_synthetic_stage(name)
    context = dict(account_id='SIM_ACCOUNT', currency='USD', timezone='UTC',
                   date_start='2026-11-16', date_stop='2026-11-16', level='ad',
                   attribution='UNIFIED_ADSET', action_report_time='impression',
                   breakdowns=[], api_version='v27.0', time_increment=1)
    rows = [dict(account_id='SIM_ACCOUNT', ad_id='SIM_AD', date_start='2026-11-16',
                 date_stop='2026-11-16', spend='10.00', impressions='1000', clicks='20')]
    plan = ReadPlan('insights', 'SIM_ACCOUNT', account_id='SIM_ACCOUNT',
                    since=context['date_start'], until=context['date_stop'])
    read = ReadSession(plan, MockTransport([(200, {'data': rows})])).collect(1)
    save('02-read.json', read)
    report = aggregate_ads(read['rows'], context, context, 'COMPLETE')
    save('03-analysis.json', {'mode': 'MOCK', 'context': context, 'report': report})
    verify_synthetic_stage('ads_read')
    handoff = {
        'runtime_status': 'MOCK_SYNTHETIC_ONLY', 'decision': '演示固定预算Diff；不生成真实投放建议',
        'evidence_observed': ['SIM_EVIDENCE：固定合成花费10.00、展示1000、点击20'],
        'evidence_labels': ['ORIGINAL_SYNTHETIC', 'NO_REAL_ACCOUNT'],
        'unknowns': ['真实账户表现、购买定义、转化价值、直播及客户素材均未验证'],
        'earliest_credible_breakpoint': '合成数据不支持真实预算决策',
        'one_minimum_validation_action': '核对模拟对象预算从10到12，状态保持SIM_PAUSED',
        'check_result_fields': ['before', 'after', 'status', 'state_version', 'action_status'],
        'maximum_loss': '真实账户无动作；仅写入这个新演示目录',
        'rollback': '保留全部记录，停止模拟账本；不自动删除或重放',
        'safe_stop': '账本stop；运行器stop并确认STOPPED',
        'stop_conditions': ['来源hash错误', '状态版本变化', '账本损坏', '结果UNKNOWN'],
        'causal_claim_allowed': False, 'live_action_authorized': False,
    }
    validate_business_handoff(handoff)
    save('04-business-handoff.json', handoff)
    save('05-gates.json', {
        'proposal': gate('proposal', dict(business_material_admitted=True, method_source_verified=True, evidence_labeled=True)),
        'live': gate('live_identify', {'existing_live_available': False}),
    })
    ledger = DurableApprovalSimulation(root / 'approval-state')
    ledger.register('SIM_AD', 'SIM_ACCOUNT', '10')
    proposal = ledger.propose('SIM_ACTION', 'SIM_AD', '12', 'SIM_EVIDENCE', '2026-11-17T12:00:00Z')
    save('06-proposal.json', proposal)
    verify_synthetic_stage('proposal')
    approval = ledger.approve('SIM_ACTION', '2026-11-17T12:00:01Z', '2026-11-17T12:05:00Z', '15')
    save('07-simulated-approval.json', approval)
    result = ledger.execute('SIM_ACTION', '2026-11-17T12:00:02Z')
    ledger = DurableApprovalSimulation(root / 'approval-state')
    readback = ledger.reconcile('SIM_ACTION')
    replay = ledger.execute('SIM_ACTION', '2026-11-18T12:00:00Z')
    ledger.stop()
    save('08-readback.json', {'execute': result, 'reconcile': readback, 'repeat': replay, 'status': ledger.status()})
    verify_synthetic_stage('approval_simulation')
    runtime = LocalRuntime(root / 'runtime-state')
    runtime.run(.05, 2)
    runtime.control('stop')
    save('09-runtime.json', runtime.status())
    for name in ('monitor', 'review', 'exit'): verify_synthetic_stage(name)
    workflow.stop()
    save('10-workflow.json', workflow.status())
    remote = MockCreationTransport(root / 'creation-service-state')
    remote.seed_account('SIM_ACCOUNT')
    creation = DurablePausedCreation(root / 'creation-operator-state', remote, SimulatedApprovalVerifier())
    packets = []; consents = []; results = []
    synthetic_brief = {'scope': 'Original synthetic fixture only', 'text': 'Example tray with three compartments',
                       'image': 'NOT_PRODUCED', 'rights': 'NOT_VERIFIED_FOR_PUBLICATION'}
    for level, parent in [('CAMPAIGN', None), ('ADSET', 'SIM_CAMPAIGN'), ('AD', 'SIM_ADSET')]:
        obj = dict(account_id='SIM_ACCOUNT', object_id='SIM_' + level, level=level, parent_id=parent,
                   currency='USD', daily_budget_minor=1000 if level == 'ADSET' else None,
                   creative_hash=digest(synthetic_brief) if level == 'AD' else None, status='PAUSED')
        packet = creation_proposal('SIM_CREATE_' + level, obj, 1, 1 if parent else None,
                          'SIM_CREATION_EVIDENCE', '2026-11-17T12:00:00Z')
        creation.submit(packet); packets.append(packet)
        consents.append(creation.simulated_approval('SIM_CREATE_' + level,
                        '2026-11-17T12:00:01Z', '2026-11-17T12:05:00Z', 1000 if level == 'ADSET' else 0))
        results.append(creation.execute('SIM_CREATE_' + level, '2026-11-17T12:00:02Z'))
    creation.stop()
    save('11-paused-creation.json', {'synthetic_brief': synthetic_brief, 'proposals': packets,
         'simulated_approvals': consents, 'results': results, 'operator': creation.status(),
         'simulated_service': remote.state(), 'actual_meta_payload_validated': False})
    receipt = {
        'schema': 'synthetic-walkthrough-v1', 'status': 'SYNTHETIC_WALKTHROUGH_PASSED',
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'fixture_clock': 'Fixed 2026-11-17 UTC; this is not a live observation time',
        'mode': 'MOCK', 'human_identity_verified': False, 'live_action_authorized': False,
        'real_connection_verified': False, 'real_live': 'NOT_RUN',
        'method_use': 'Hash verified only; no specialist business execution or upstream engine',
        'artifacts': list(artifacts),
    }
    save('RECEIPT.json', receipt)
    return receipt
