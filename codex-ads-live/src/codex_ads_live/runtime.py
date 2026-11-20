"""Foreground local MOCK runner. Never schedules real calls or live writes."""
from copy import deepcopy
from datetime import datetime, timezone
import re
import time
import uuid
from .journal import Journal
from .read_adapter import MockTransport, ReadPlan, ReadSession
from .validation import aware_time, fail, finite_number, object_only

PHASES={"STOPPED","RUNNING","PAUSED","INTERRUPTED"}
ERRORS={None,"STALE_HEARTBEAT","RECOVERY_REQUIRED","CLOCK_ROLLBACK","SIMULATED_UNAVAILABLE","WORKER_INTERRUPTED"}


def now(): return datetime.now(timezone.utc).isoformat()


def initial():
    return {"schema_version":1,"mode":"MOCK","phase":"STOPPED","worker":None,"interval_seconds":1,
            "last_heartbeat":None,"last_successful_sync":{"ads":None,"live":None,"comments":None},
            "last_error":None,"cycle_count":0,"last_row_count":0,"last_result":"NOT_RUN"}


def validate_state(state):
    object_only(state,initial(),initial())
    if type(state["schema_version"]) is not int or state["schema_version"]!=1 or state["mode"]!="MOCK": fail("STATE_SCHEMA")
    if state["phase"] not in PHASES or state["last_error"] not in ERRORS: fail("STATE_ENUM")
    if state["worker"] is not None and (type(state["worker"]) is not str or not re.fullmatch(r"[a-f0-9]{32}",state["worker"])): fail("WORKER_ID_INVALID")
    if not finite_number(state["interval_seconds"]) or not .05<=state["interval_seconds"]<=60: fail("INTERVAL_INVALID")
    for field in ("cycle_count","last_row_count"):
        if type(state[field]) is not int or state[field]<0: fail("STATE_COUNT")
    if state["last_result"] not in ("NOT_RUN","EMPTY","COMPLETE","ERROR"): fail("STATE_RESULT")
    object_only(state["last_successful_sync"],("ads","live","comments"),("ads","live","comments"))
    for stamp in (state["last_heartbeat"],*state["last_successful_sync"].values()):
        if stamp is not None: aware_time(stamp)
    if state["phase"] in ("RUNNING","PAUSED") and (state["worker"] is None or state["last_heartbeat"] is None): fail("ACTIVE_STATE_REQUIRES_WORKER")


class LocalRuntime:
    def __init__(self,directory,clock=now): self.journal=Journal(directory);self.clock=clock

    def _load(self):
        state,damaged=self.journal.load(validate_state)
        state=state or initial()
        if damaged:
            state["phase"]="INTERRUPTED";state["last_error"]="RECOVERY_REQUIRED";state["worker"]=None
        return state,damaged

    def _observed(self,state):
        state=deepcopy(state)
        if state["phase"] in ("RUNNING","PAUSED"):
            age=(aware_time(self.clock())-aware_time(state["last_heartbeat"])).total_seconds()
            if age<0:
                state["phase"]="INTERRUPTED";state["last_error"]="CLOCK_ROLLBACK"
            elif age>max(5,3*state["interval_seconds"]):
                state["phase"]="INTERRUPTED";state["last_error"]="STALE_HEARTBEAT"
        return state

    def status(self):
        with self.journal.locked(wait_seconds=.5):
            state,damaged=self._load();observed=self._observed(state)
        # Don't expose an internal lease ID. Status observes staleness but does
        # not change a snapshot or claim that a remote ad/live has stopped.
        observed.pop("worker")
        observed.update(health="RECOVERY_REQUIRED" if damaged else ("LOCAL_LOOP_ALIVE" if observed["phase"] in ("RUNNING","PAUSED") else observed["phase"]),
                        damaged_snapshot_count=len(damaged),real_connection_verified=False)
        return observed

    def control(self,action):
        if action not in ("pause","stop"): fail("CONTROL_ACTION")
        with self.journal.locked(wait_seconds=.5):
            state,_=self._load()
            if action=="pause":
                if self._observed(state)["phase"]!="RUNNING": fail("PAUSE_REQUIRES_ACTIVE_WORKER")
                state["phase"]="PAUSED"
            else:
                state["phase"]="STOPPED";state["worker"]=None
            self.journal.save(state,validate_state)
        return self.status()

    def claim(self,interval_seconds=1,resume=False):
        if not finite_number(interval_seconds) or not .05<=interval_seconds<=60: fail("INTERVAL_INVALID")
        with self.journal.locked(wait_seconds=.5):
            state,damaged=self._load();observed=self._observed(state)
            if observed["phase"]=="RUNNING": fail("RUNNER_ALREADY_ACTIVE")
            if not resume and (observed["phase"] in ("PAUSED","INTERRUPTED") or damaged): fail("EXPLICIT_RESUME_REQUIRED")
            state.update(phase="RUNNING",worker=uuid.uuid4().hex,last_heartbeat=self.clock(),interval_seconds=interval_seconds,last_error=None)
            self.journal.save(state,validate_state)
            return state["worker"]

    def tick(self,worker,simulate_failure=False):
        with self.journal.locked(wait_seconds=.5):
            state,_=self._load()
            if state["worker"]!=worker or state["phase"] not in ("RUNNING","PAUSED"): return False
            stamp=self.clock()
            if aware_time(stamp)<aware_time(state["last_heartbeat"]):
                state.update(phase="INTERRUPTED",last_error="CLOCK_ROLLBACK",worker=None)
                self.journal.save(state,validate_state);return False
            state["last_heartbeat"]=stamp
            if state["phase"]=="RUNNING":
                # Fixed synthetic transport is deliberately not configurable to
                # a live URL, a customer fixture or a credential provider.
                p=ReadPlan("ads","SIM_ACCOUNT",account_id="SIM_ACCOUNT")
                payload={"error":{"code":2}} if simulate_failure else {"data":[{"id":"SIM_AD","account_id":"SIM_ACCOUNT","status":"PAUSED"}]}
                result=ReadSession(p,MockTransport([(503 if simulate_failure else 200,payload)])).collect()
                state["cycle_count"]+=1;state["last_result"]=result["state"];state["last_row_count"]=len(result["rows"])
                if result["state"] in ("COMPLETE","EMPTY"):
                    state["last_successful_sync"]["ads"]=stamp;state["last_error"]=None
                else:
                    state["last_error"]="SIMULATED_UNAVAILABLE";state["phase"]="PAUSED"
            self.journal.save(state,validate_state)
            return True

    def finish(self,worker,interrupted=False):
        with self.journal.locked(wait_seconds=.5):
            state,_=self._load()
            if state["worker"]!=worker:return
            state.update(phase="INTERRUPTED" if interrupted else "STOPPED",worker=None)
            if interrupted:state["last_error"]="WORKER_INTERRUPTED"
            self.journal.save(state,validate_state)

    def run(self,interval_seconds=1,cycles=None,resume=False):
        if cycles is not None and (type(cycles) is not int or not 1<=cycles<=10000):fail("CYCLE_BUDGET")
        worker=self.claim(interval_seconds,resume)
        interrupted=True
        try:
            count=0
            while self.tick(worker):
                count+=1
                if cycles is not None and count>=cycles:break
                time.sleep(interval_seconds)
            interrupted=False
        finally:self.finish(worker,interrupted)
        return self.status()
