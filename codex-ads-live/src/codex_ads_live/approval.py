"""Version-bound MOCK approvals built on the frozen internal T0 core.

This is not a human identity verifier. SIM_REVIEWER approvals cannot be used
against a real transport; actual write tools do not exist in this package.
"""
from copy import deepcopy
from datetime import timedelta
from .vendor import t0_core as t0
from .validation import aware_time, fail
from .read_adapter import numeric_string


class MemoryCheckpoint:
    def __init__(self):self.snapshots=[]
    def save(self,state):self.snapshots.append(deepcopy(state))


class ApprovalSimulation:
    def __init__(self):
        self.state=t0.new_state()
        self.store=MemoryCheckpoint()
        self.applied_versions=set()
        self.revocations={}

    def register(self,object_id,account_id,budget,currency="USD"):
        t0.sim_id(object_id);t0.sim_id(account_id);numeric_string(budget)
        if currency not in ("USD","EUR","JPY"):fail("MOCK_CURRENCY_UNSUPPORTED")
        if object_id in self.state["sim_objects"]:fail("OBJECT_ALREADY_REGISTERED")
        self.state["sim_objects"][object_id]={"account_id":account_id,"budget":budget,"currency":currency,
                                               "status":"SIM_PAUSED","state_version":1}

    def propose(self,action_id,object_id,after,evidence_id,evidence_at):
        numeric_string(after)
        if object_id not in self.state["sim_objects"]:fail("UNKNOWN_SIM_OBJECT")
        obj=self.state["sim_objects"][object_id]
        proposal=t0.propose(action_id,object_id,obj["account_id"],obj["currency"],obj["budget"],after,evidence_id,evidence_at)
        proposal["plan"].update(state_version=obj["state_version"],expected_status="SIM_PAUSED",unit="MAJOR_CURRENCY_SIMULATION_ONLY")
        proposal["plan_hash"]=t0.digest(proposal["plan"])
        proposal["diff"]={"budget":{"before":obj["budget"],"after":after},"status":{"before":"SIM_PAUSED","after":"SIM_PAUSED"}}
        proposal["mode"]="MOCK"
        return proposal

    def approve(self,proposal,approved_at,expires_at,max_budget,reviewer="SIM_REVIEWER"):
        self._check_diff(proposal)
        if not timedelta(0)<aware_time(expires_at)-aware_time(approved_at)<=timedelta(minutes=15):fail("APPROVAL_WINDOW_INVALID")
        if proposal['plan']['action_id'] in self.revocations:fail('APPROVAL_REVOKED')
        if aware_time(approved_at)<aware_time(proposal["plan"]["evidence_at"]):fail("APPROVAL_BEFORE_EVIDENCE")
        if proposal["plan_hash"]!=t0.digest(proposal["plan"]):fail("PLAN_CHANGED")
        approval=t0.approve(proposal,reviewer,expires_at,max_budget)
        approval.update(approved_at=approved_at,mode="MOCK",human_identity_verified=False)
        return approval

    def _check(self,proposal,approval,at):
        self._check_diff(proposal)
        plan=proposal["plan"]
        if plan['action_id'] in self.revocations:fail('APPROVAL_REVOKED')
        if proposal.get("mode")!="MOCK" or approval.get("mode")!="MOCK":fail("MOCK_ONLY")
        if proposal["plan_hash"]!=t0.digest(plan) or approval["plan_hash"]!=proposal["plan_hash"]:fail("PLAN_CHANGED")
        if aware_time(at)<aware_time(approval["approved_at"]):fail("APPROVAL_NOT_YET_VALID")
        if plan["action_id"] in self.state["actions"]:
            if self.state["actions"][plan["action_id"]]["plan_hash"]!=proposal["plan_hash"]:fail("ACTION_ID_CONFLICT")
            return  # existing UNKNOWN/VERIFIED is read back, never replayed
        obj=self.state["sim_objects"].get(plan["object_id"])
        if obj is None or obj["state_version"]!=plan["state_version"]:fail("STATE_VERSION_CHANGED")
        if obj["status"]!=plan["expected_status"] or plan["expected_status"]!="SIM_PAUSED":fail("STATUS_CHANGED")

    @staticmethod
    def _check_diff(proposal):
        plan=proposal["plan"]
        expected={"budget":{"before":plan["before"],"after":plan["after"]},"status":{"before":"SIM_PAUSED","after":"SIM_PAUSED"}}
        if proposal.get("diff")!=expected:fail("DIFF_DOES_NOT_MATCH_PLAN")

    def execute(self,proposal,approval,at,failure=None):
        self._check(proposal,approval,at)
        result=t0.execute_sim(self.state,proposal,approval,at,self.store,failure)
        self._advance_version_once(proposal,result)
        return {"status":result,"mode":"MOCK","live_action_authorized":False}

    def reconcile(self,proposal):
        self._check_diff(proposal)
        plan=proposal["plan"]
        obj=self.state["sim_objects"].get(plan["object_id"])
        if obj is None or obj["account_id"]!=plan["account_id"] or obj["currency"]!=plan["currency"] or obj["status"]!="SIM_PAUSED":
            fail("READBACK_CONTEXT_CHANGED")
        if plan["action_id"] not in self.applied_versions and obj["state_version"]!=plan["state_version"]:
            fail("READBACK_REVISION_CHANGED")
        result=t0.reconcile(self.state,proposal,self.store)
        self._advance_version_once(proposal,result)
        return {"status":result,"mode":"MOCK","live_action_authorized":False}

    def _advance_version_once(self,proposal,result):
        action=proposal["plan"]["action_id"]
        if result=="VERIFIED_SIM" and action not in self.applied_versions:
            self.state["sim_objects"][proposal["plan"]["object_id"]]["state_version"]+=1
            self.applied_versions.add(action)
            self.store.save(self.state)

    def simulate_external_revision(self,object_id):
        t0.sim_id(object_id)
        self.state["sim_objects"][object_id]["state_version"]+=1

    def revoke(self,proposal,at):
        self._check_diff(proposal);action=proposal['plan']['action_id']
        if action in self.state['actions']:fail('APPROVAL_EXECUTION_ALREADY_STARTED')
        if aware_time(at)<aware_time(proposal['plan']['evidence_at']):fail('APPROVAL_REVOKE_BEFORE_EVIDENCE')
        if action not in self.revocations:self.revocations[action]=at

    def pause(self):self.state["runtime"]="STOPPED"
