"""Persistent synthetic workflow metadata; no state grants real permission."""
from copy import deepcopy
from .journal import Journal
from .validation import fail, object_only
from .workflow import GATES, gate
from .vendor.t0_core import sim_id

DEPENDENCIES = {
    'onboarding': (), 'asset_inventory': ('onboarding',), 'app_setup': ('asset_inventory',),
    'connection': ('app_setup',), 'ads_read': ('connection',), 'live_identify': ('connection',),
    'comments_read': ('live_identify',), 'live_metrics': ('live_identify',),
    'proposal': ('onboarding',), 'approval_simulation': ('proposal',),
    'monitor': ('onboarding',), 'review': ('onboarding',), 'exit': (),
}
PHASES = {'NEEDS_EVIDENCE', 'READY_FOR_REVIEW', 'RUNNING', 'VERIFIED', 'PARTIAL', 'WAITING_EXTERNAL', 'STOPPED'}


def fresh():
    return {'schema': 'synthetic-workflow-v1', 'revision': 1, 'mode': 'MOCK',
            'stages': {name: {'phase': 'NEEDS_EVIDENCE', 'evidence': {}, 'result_ids': [], 'run_revision': None}
                       for name in GATES}}


def validate(value):
    object_only(value, {'schema', 'revision', 'mode', 'stages'}, {'schema', 'revision', 'mode', 'stages'})
    if value['schema'] != 'synthetic-workflow-v1' or value['mode'] != 'MOCK': fail('WORKFLOW_MOCK_ONLY')
    if type(value['revision']) is not int or not 1 <= value['revision'] <= 100000: fail('WORKFLOW_REVISION')
    object_only(value['stages'], GATES, GATES)
    for name, stage in value['stages'].items():
        object_only(stage, {'phase', 'evidence', 'result_ids', 'run_revision'}, {'phase', 'evidence', 'result_ids', 'run_revision'})
        if stage['phase'] not in PHASES: fail('WORKFLOW_PHASE')
        checked = gate(name, stage['evidence'])
        if stage['phase'] in ('READY_FOR_REVIEW', 'RUNNING', 'VERIFIED') and checked['missing_evidence']:
            fail('WORKFLOW_EVIDENCE_INCONSISTENT')
        if stage['run_revision'] is not None and (type(stage['run_revision']) is not int or not 1 <= stage['run_revision'] <= value['revision']):
            fail('WORKFLOW_RUN_REVISION')
        if type(stage['result_ids']) is not list or len(stage['result_ids']) > 100: fail('WORKFLOW_RESULT_IDS')
        for identifier in stage['result_ids']: sim_id(identifier)
        if stage['phase'] == 'VERIFIED' and not stage['result_ids']: fail('WORKFLOW_RESULT_REQUIRED')
        if stage['phase'] in ('RUNNING', 'VERIFIED'):
            if stage['run_revision'] is None: fail('WORKFLOW_RUN_REQUIRED')
            if any(value['stages'][dep]['phase'] != 'VERIFIED' for dep in DEPENDENCIES[name]): fail('WORKFLOW_DEPENDENCY_INCONSISTENT')


class SyntheticWorkflow:
    def __init__(self, directory): self.journal = Journal(directory)

    def _load(self):
        data, damaged = self.journal.load(validate)
        if damaged: fail('WORKFLOW_RECOVERY_REVIEW_REQUIRED')
        return data if data is not None else fresh()

    @staticmethod
    def _name(name):
        if type(name) is not str or name not in GATES: fail('UNKNOWN_WORKFLOW')

    def status(self):
        with self.journal.locked():
            data = self._load()
            return {**data, 'live_action_authorized': False, 'real_connection_verified': False,
                    'evidence_semantics': 'SYNTHETIC_TEST_ASSERTIONS_NOT_REAL_ACCEPTANCE'}

    def set_evidence(self, name, evidence):
        self._name(name); gate(name, evidence)
        with self.journal.locked():
            data = self._load()
            if data['stages'][name]['evidence'] == evidence: return data['revision']
            affected = {name}
            while True:
                expanded = affected | {stage for stage, deps in DEPENDENCIES.items() if affected.intersection(deps)}
                if expanded == affected: break
                affected = expanded
            data['revision'] += 1
            data['stages'][name]['evidence'] = deepcopy(evidence)
            for stage in affected:
                item = data['stages'][stage]
                item.update(phase=gate(stage, item['evidence'])['stage'], result_ids=[], run_revision=None)
            self.journal.save(data, validate)
            return data['revision']

    def begin(self, name):
        self._name(name)
        with self.journal.locked():
            data = self._load(); stage = data['stages'][name]
            if stage['phase'] not in ('READY_FOR_REVIEW', 'PARTIAL', 'WAITING_EXTERNAL', 'STOPPED'):
                fail('WORKFLOW_NOT_READY')
            if gate(name, stage['evidence'])['missing_evidence']: fail('WORKFLOW_EVIDENCE_REQUIRED')
            if any(data['stages'][dep]['phase'] != 'VERIFIED' for dep in DEPENDENCIES[name]): fail('WORKFLOW_DEPENDENCY_REQUIRED')
            data['revision'] += 1
            stage.update(phase='RUNNING', run_revision=data['revision'], result_ids=[])
            self.journal.save(data, validate)
            return data['revision']

    def finish(self, name, run_revision, phase, result_ids):
        self._name(name)
        if type(phase) is not str or phase not in ('VERIFIED', 'PARTIAL', 'WAITING_EXTERNAL', 'STOPPED'): fail('WORKFLOW_FINISH_PHASE')
        if type(run_revision) is not int: fail('WORKFLOW_STALE_RUN')
        with self.journal.locked():
            data = self._load(); stage = data['stages'][name]
            if stage['phase'] != 'RUNNING' or stage['run_revision'] != run_revision: fail('WORKFLOW_STALE_RUN')
            data['revision'] += 1
            stage.update(phase=phase, result_ids=deepcopy(result_ids))
            self.journal.save(data, validate)

    def stop(self):
        with self.journal.locked():
            data = self._load(); data['revision'] += 1
            for stage in data['stages'].values():
                if stage['phase'] == 'RUNNING': stage.update(phase='STOPPED', result_ids=[])
            self.journal.save(data, validate)
