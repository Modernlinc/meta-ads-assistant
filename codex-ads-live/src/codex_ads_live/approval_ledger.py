"""Durable synthetic approval transactions. No real write transport or identity.

UNKNOWN is saved before the simulated side effect. Damaged tails block all
mutations rather than silently restoring an older approval and replaying it.
"""
from copy import deepcopy
from datetime import timedelta
import re
from .approval import ApprovalSimulation
from .journal import Journal,digest
from .read_adapter import numeric_string
from .validation import aware_time,fail,object_only
from .vendor import t0_core as t0


def exact(value,keys):object_only(value,keys,keys)
def sim(value):return t0.sim_id(value)
def hashed(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}',value):fail('LEDGER_HASH')
def bounded_map(value):
    if type(value) is not dict or len(value)>100:fail('LEDGER_COLLECTION')


def validate_proposal(proposal):
    exact(proposal,{'plan','plan_hash','status','diff','mode'})
    plan=proposal['plan']
    exact(plan,{'action_id','kind','object_id','account_id','currency','before','after','evidence_id','evidence_at','state_version','expected_status','unit'})
    for key in ('action_id','object_id','account_id','evidence_id'):sim(plan[key])
    for key in ('before','after'):numeric_string(plan[key])
    aware_time(plan['evidence_at'])
    if type(plan['state_version']) is not int or not 1<=plan['state_version']<=100000:fail('LEDGER_VERSION')
    if plan['kind']!='SIM_SET_BUDGET' or plan['expected_status']!='SIM_PAUSED' or plan['currency'] not in ('USD','EUR','JPY') or plan['unit']!='MAJOR_CURRENCY_SIMULATION_ONLY':fail('LEDGER_PLAN_SCOPE')
    if proposal['mode']!='MOCK' or proposal['status']!='PROPOSED':fail('LEDGER_PROPOSAL_SCOPE')
    hashed(proposal['plan_hash'])
    if digest(plan)!=proposal['plan_hash']:fail('LEDGER_PROPOSAL_CHANGED')
    ApprovalSimulation._check_diff(proposal)


def validate_approval(value,proposal):
    exact(value,{'plan_hash','reviewer','expires_at','max_budget','approved_at','mode','human_identity_verified'})
    if value['plan_hash']!=proposal['plan_hash'] or value['reviewer']!='SIM_REVIEWER' or value['mode']!='MOCK' or value['human_identity_verified'] is not False:fail('LEDGER_APPROVAL_SCOPE')
    numeric_string(value['max_budget'])
    if not aware_time(proposal['plan']['evidence_at'])<=aware_time(value['approved_at'])<aware_time(value['expires_at']):fail('LEDGER_APPROVAL_TIME')
    if aware_time(value['expires_at'])-aware_time(value['approved_at'])>timedelta(minutes=15):fail('LEDGER_APPROVAL_TIME')


def validate_ledger(data):
    required={'schema','mode','state','applied_versions','proposals','approvals'}
    object_only(data,required|{'revocations'},required)
    if data['schema']!='approval-ledger-v1' or data['mode']!='MOCK':fail('LEDGER_SCOPE')
    state=data['state'];exact(state,t0.new_state())
    if state['schema']!='t0-v1' or state['synthetic'] is not True or state['runtime'] not in ('RUNNING','STOPPED') or type(state['recovery_required']) is not bool:fail('LEDGER_STATE_SCOPE')
    if state['heartbeat'] is not None or state['last_success']!={'ads':None,'live':None,'comments':None} or state['comments']!={}:fail('LEDGER_UNUSED_STATE')
    for name in ('sim_objects','actions','receipts'):bounded_map(state[name])
    for identifier,obj in state['sim_objects'].items():
        sim(identifier);exact(obj,{'account_id','budget','currency','status','state_version'});sim(obj['account_id']);numeric_string(obj['budget'])
        if obj['currency'] not in ('USD','EUR','JPY') or obj['status']!='SIM_PAUSED' or type(obj['state_version']) is not int or not 1<=obj['state_version']<=100000:fail('LEDGER_OBJECT_SCOPE')
    for name in ('proposals','approvals'):bounded_map(data[name])
    for action,proposal in data['proposals'].items():
        sim(action);validate_proposal(proposal)
        if action!=proposal['plan']['action_id'] or proposal['plan']['object_id'] not in state['sim_objects']:fail('LEDGER_PROPOSAL_RELATION')
    for action,approval in data['approvals'].items():
        if action not in data['proposals']:fail('LEDGER_APPROVAL_RELATION')
        validate_approval(approval,data['proposals'][action])
    revoked=data.get('revocations',{});bounded_map(revoked)
    for action,at in revoked.items():
        if action not in data['proposals'] or action in state['actions']:fail('LEDGER_REVOCATION_RELATION')
        if aware_time(at)<aware_time(data['proposals'][action]['plan']['evidence_at']):fail('LEDGER_REVOCATION_TIME')
        if action in data['approvals'] and aware_time(at)<aware_time(data['approvals'][action]['approved_at']):fail('LEDGER_REVOCATION_TIME')
    for action,value in state['actions'].items():
        sim(action);exact(value,{'plan_hash','status'});hashed(value['plan_hash'])
        if action not in data['proposals'] or action not in data['approvals'] or value['plan_hash']!=data['proposals'][action]['plan_hash'] or value['status'] not in ('UNKNOWN','VERIFIED_SIM'):fail('LEDGER_ACTION_RELATION')
    for action,receipt in state['receipts'].items():
        exact(receipt,{'plan_hash','object_id','after'})
        if action not in state['actions']:fail('LEDGER_RECEIPT_RELATION')
        p=data['proposals'][action]
        if receipt!={'plan_hash':p['plan_hash'],'object_id':p['plan']['object_id'],'after':p['plan']['after']}:fail('LEDGER_RECEIPT_CHANGED')
    applied=data['applied_versions']
    if type(applied) is not list or len(applied)!=len(set(applied)) or not set(applied)<=set(state['actions']):fail('LEDGER_APPLIED_SET')
    for action in applied:
        if state['actions'][action]['status']!='VERIFIED_SIM':fail('LEDGER_APPLIED_UNVERIFIED')
    if type(state['events']) is not list or len(state['events'])>1000:fail('LEDGER_EVENTS')
    for event in state['events']:
        exact(event,{'code','object_id'});sim(event['object_id'])
        if event['code'] not in ('ACTION_UNKNOWN','ACTION_VERIFIED'):fail('LEDGER_EVENT_SCOPE')


class _Checkpoint:
    def __init__(self,journal,engine,proposals,approvals):
        self.journal=journal;self.engine=engine;self.proposals=proposals;self.approvals=approvals
    def save(self,state):
        data={'schema':'approval-ledger-v1','mode':'MOCK','state':deepcopy(state),'applied_versions':sorted(self.engine.applied_versions),
              'proposals':deepcopy(self.proposals),'approvals':deepcopy(self.approvals),'revocations':deepcopy(self.engine.revocations)}
        self.journal.save(data,validate_ledger)


class DurableApprovalSimulation:
    def __init__(self,directory):self.journal=Journal(directory)

    def _load(self,allow_damaged=False):
        data,damaged=self.journal.load(validate_ledger)
        if damaged and not allow_damaged:fail('LEDGER_RECOVERY_REVIEW_REQUIRED')
        engine=ApprovalSimulation();proposals={};approvals={}
        if data is not None:
            engine.state=deepcopy(data['state']);engine.applied_versions=set(data['applied_versions'])
            engine.revocations=deepcopy(data.get('revocations',{}))
            proposals=deepcopy(data['proposals']);approvals=deepcopy(data['approvals'])
        engine.store=_Checkpoint(self.journal,engine,proposals,approvals)
        return engine,proposals,approvals,damaged

    def status(self):
        with self.journal.locked():
            engine,proposals,approvals,damaged=self._load(allow_damaged=True)
            return {'mode':'MOCK','live_action_authorized':False,'human_identity_verified':False,
                    'state':'RECOVERY_REVIEW_REQUIRED' if damaged else engine.state['runtime'],
                    'objects':deepcopy(engine.state['sim_objects']),'actions':deepcopy(engine.state['actions']),
                    'proposals':sorted(proposals),'approvals':sorted(approvals),'revocations':deepcopy(engine.revocations),'damaged_snapshots':damaged}

    def inspect(self,action_id):
        sim(action_id)
        with self.journal.locked():
            engine,proposals,approvals,_=self._load()
            if action_id not in proposals:fail('PROPOSAL_NOT_FOUND')
            proposal=proposals[action_id]
            return deepcopy(dict(proposal=proposal,approval=approvals.get(action_id),
                revoked_at=engine.revocations.get(action_id),
                status=engine.state['actions'].get(action_id,{}).get('status') or
                    ('REVOKED_SIM' if action_id in engine.revocations else 'APPROVED_SIM' if action_id in approvals else 'PROPOSED'),
                runtime=engine.state['runtime'],object=engine.state['sim_objects'][proposal['plan']['object_id']]))

    def revoke(self,action_id,at):
        sim(action_id)
        with self.journal.locked():
            engine,proposals,approvals,_=self._load()
            if action_id not in proposals:fail('PROPOSAL_NOT_FOUND')
            if action_id in approvals and aware_time(at)<aware_time(approvals[action_id]['approved_at']):fail('LEDGER_REVOCATION_TIME')
            engine.revoke(proposals[action_id],at);engine.store.save(engine.state)

    def register(self,object_id,account_id,budget,currency='USD'):
        with self.journal.locked():
            engine,*_=self._load();engine.register(object_id,account_id,budget,currency);engine.store.save(engine.state)

    def propose(self,action_id,object_id,after,evidence_id,evidence_at):
        with self.journal.locked():
            engine,proposals,_,_=self._load()
            if action_id in proposals:fail('PROPOSAL_ALREADY_EXISTS')
            proposal=engine.propose(action_id,object_id,after,evidence_id,evidence_at);validate_proposal(proposal)
            proposals[action_id]=proposal;engine.store.save(engine.state);return deepcopy(proposal)

    def proposal(self,action_id):
        sim(action_id)
        with self.journal.locked():
            _,proposals,_,_=self._load()
            if action_id not in proposals:fail('PROPOSAL_NOT_FOUND')
            return deepcopy(proposals[action_id])

    def approve(self,action_id,approved_at,expires_at,max_budget,reviewer='SIM_REVIEWER'):
        sim(action_id);numeric_string(max_budget)
        with self.journal.locked():
            engine,proposals,approvals,_=self._load()
            if action_id not in proposals:fail('PROPOSAL_NOT_FOUND')
            if action_id in approvals:fail('APPROVAL_ALREADY_EXISTS')
            approval=engine.approve(proposals[action_id],approved_at,expires_at,max_budget,reviewer)
            approvals[action_id]=approval;engine.store.save(engine.state);return deepcopy(approval)

    def execute(self,action_id,at,failure=None):
        sim(action_id)
        with self.journal.locked():
            engine,proposals,approvals,_=self._load()
            if action_id not in proposals or action_id not in approvals:fail('APPROVAL_REQUIRED')
            return engine.execute(proposals[action_id],approvals[action_id],at,failure)

    def reconcile(self,action_id):
        sim(action_id)
        with self.journal.locked():
            engine,proposals,_,_=self._load()
            if action_id not in proposals:fail('PROPOSAL_NOT_FOUND')
            return engine.reconcile(proposals[action_id])

    def simulate_external_revision(self,object_id):
        sim(object_id)
        with self.journal.locked():
            engine,*_=self._load()
            if object_id not in engine.state['sim_objects']:fail('UNKNOWN_SIM_OBJECT')
            engine.simulate_external_revision(object_id);engine.store.save(engine.state)

    def stop(self):
        with self.journal.locked():
            engine,*_=self._load();engine.pause();engine.store.save(engine.state)
