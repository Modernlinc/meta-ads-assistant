"""T0 synthetic-only logic. No network, credentials, scheduler, or Meta writes."""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re


class ContractError(ValueError):
    """Safe, enumerated failure; never embed raw inputs in the message."""


def require(condition, code):
    if not condition:
        raise ContractError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_KEY")
            result[key] = value
        return result

    def bad_constant(_):
        raise ContractError("NON_FINITE")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)


def sim_id(value):
    require(isinstance(value, str) and re.fullmatch(r"SIM_[A-Z0-9_]{1,80}", value), "SIM_ID_REQUIRED")
    return value


def timestamp(value):
    require(isinstance(value, str), "TIME_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ContractError("TIME_INVALID") from None
    require(parsed.tzinfo is not None, "TIMEZONE_REQUIRED")
    return parsed


def number(value, integer=False):
    require(not isinstance(value, bool) and isinstance(value, (str, int)), "NUMBER_TYPE")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ContractError("NUMBER_INVALID") from None
    require(result.is_finite() and result >= 0, "NUMBER_RANGE")
    if integer:
        require(result == result.to_integral_value(), "INTEGER_REQUIRED")
    return result


def metric(value=None, *, error=None, empty=False, integer=True):
    require(error in (None, "PERMISSION", "RATE_LIMIT", "UNAVAILABLE"), "ERROR_CODE")
    if error:
        return {"value": None, "status": "ERROR", "error": error}
    if empty:
        return {"value": None, "status": "EMPTY"}
    if value is None:
        return {"value": None, "status": "MISSING"}
    n = number(value, integer)
    return {"value": int(n) if integer else str(n), "status": "VALUE"}


def freshness(data_as_of, now, max_age_seconds):
    current = timestamp(now)
    require(type(max_age_seconds) is int and max_age_seconds > 0, "AGE_LIMIT")
    if data_as_of is None:
        return "UNKNOWN"
    age = (current - timestamp(data_as_of)).total_seconds()
    if age < 0:
        return "CLOCK_SKEW"
    return "FRESH" if age <= max_age_seconds else "STALE"


CONTEXT_KEYS = {"account_id", "currency", "timezone", "date_start", "date_stop", "level", "attribution", "breakdowns"}


def validate_context(context):
    require(set(context) == CONTEXT_KEYS, "CONTEXT_FIELDS")
    sim_id(context["account_id"])
    require(context["currency"] in {"USD", "EUR", "JPY"}, "CURRENCY")
    require(isinstance(context["timezone"], str) and re.fullmatch(r"UTC[+-](?:(?:0\d|1[0-3]):[0-5]\d|14:00)", context["timezone"]), "TIMEZONE")
    require(context["level"] == "ad" and context["attribution"] == "SIM_7d_click", "GRAIN")
    require(context["breakdowns"] == [], "BREAKDOWNS_UNSUPPORTED")
    try:
        start, stop = date.fromisoformat(context["date_start"]), date.fromisoformat(context["date_stop"])
    except (ValueError, TypeError):
        raise ContractError("DATE_INVALID") from None
    require(start <= stop, "DATE_ORDER")


def aggregate_ads(rows, context, expected_context):
    validate_context(context)
    validate_context(expected_context)
    require(context == expected_context, "CONTEXT_MISMATCH")
    seen = set()
    result = {}
    for row in rows:
        key = sim_id(row["id"])
        require(key not in seen, "DUPLICATE_AD")
        seen.add(key)
    for field in ("spend", "impressions", "clicks", "purchases"):
        values = [number(r[field], field != "spend") for r in rows if r.get(field) is not None]
        total = sum(values, Decimal(0))
        value = format(total, "f") if field == "spend" else int(total)
        complete = bool(rows) and len(values) == len(rows)
        result[field] = {"value": value if complete else None, "known_subtotal": value,
                         "status": "VALUE" if complete else ("PARTIAL" if rows else "EMPTY")}
    impressions, clicks = result["impressions"]["value"], result["clicks"]["value"]
    result["ctr_percent"] = f"{Decimal(clicks) / Decimal(impressions) * 100:.2f}" if impressions and clicks is not None else None
    return result


def bind_live(live, selected_page):
    sim_id(selected_page)
    for key in ("page_id", "live_id", "owner_id"):
        sim_id(live[key])
    require(live["page_id"] == selected_page == live["owner_id"], "OWNER_MISMATCH")
    require(live["object_type"] == "LiveVideo", "OBJECT_TYPE")
    require(live["status"] in {"SIM_LIVE", "SIM_ENDED", "SIM_UNKNOWN"}, "LIVE_STATUS")
    return {"live_id": live["live_id"], "status": live["status"],
            "current_viewers": metric(live.get("current_viewers")), "views_live": metric(live.get("views_live"))}


def new_state():
    return {"schema": "t0-v1", "synthetic": True, "runtime": "RUNNING", "heartbeat": None,
            "last_success": {"ads": None, "live": None, "comments": None}, "comments": {},
            "actions": {}, "receipts": {}, "sim_objects": {}, "events": [], "recovery_required": False}


def record_event(state, code, object_id=None):
    require(code in {"PAGE_SAVED", "STOP", "RESUME", "ACTION_UNKNOWN", "ACTION_VERIFIED", "INTERRUPTED"}, "EVENT_CODE")
    event = {"code": code}
    if object_id is not None:
        event["object_id"] = sim_id(object_id)
    state["events"].append(event)


def tick(state, now, stream=None):
    timestamp(now)
    require(state["runtime"] == "RUNNING", "NOT_RUNNING")
    require(state["heartbeat"] is None or timestamp(now) >= timestamp(state["heartbeat"]), "CLOCK_ROLLBACK")
    if stream is not None:
        require(stream in state["last_success"], "STREAM")
        state["last_success"][stream] = now
    state["heartbeat"] = now


def commit(state, candidate, store):
    store.save(candidate)
    state.clear()
    state.update(candidate)


def stop(state, store):
    candidate = deepcopy(state)
    candidate["runtime"] = "STOPPED"
    record_event(candidate, "STOP")
    # A stop also takes effect in memory if the disk is unavailable.
    try:
        commit(state, candidate, store)
    except OSError:
        state["runtime"] = "STOPPED"
        state["recovery_required"] = True
        raise


def resume(state, now, store):
    require(state["runtime"] in {"STOPPED", "INTERRUPTED"}, "RESUME_STATE")
    timestamp(now)
    require(state["heartbeat"] is None or timestamp(now) >= timestamp(state["heartbeat"]), "CLOCK_ROLLBACK")
    candidate = deepcopy(state)
    candidate["runtime"] = "RUNNING"
    tick(candidate, now)
    record_event(candidate, "RESUME")
    commit(state, candidate, store)


def check_heartbeat(state, now, max_age_seconds, store):
    if state["runtime"] == "RUNNING" and freshness(state["heartbeat"], now, max_age_seconds) != "FRESH":
        candidate = deepcopy(state)
        candidate["runtime"] = "INTERRUPTED"
        record_event(candidate, "INTERRUPTED")
        commit(state, candidate, store)
    return state["runtime"]


class SnapshotStore:
    """Append-only, single-writer fixture checkpoints; damaged tails fail closed."""
    def __init__(self, directory):
        self.directory = Path(directory)
        require(not self.directory.is_symlink(), "SYMLINK")
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, state):
        require(state.get("synthetic") is True and state.get("schema") == "t0-v1", "STATE_SCOPE")
        files = sorted(self.directory.glob("snapshot-*.json"))
        sequence = max([int(p.stem.split("-")[1]) for p in files] or [0]) + 1
        data = {"payload": state, "sha256": digest(state)}
        destination = self.directory / f"snapshot-{sequence:06d}.json"
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(canonical(data))
            handle.flush()
            os.fsync(handle.fileno())
        return destination

    def load(self):
        damaged = []
        for path in sorted(self.directory.glob("snapshot-*.json"), reverse=True):
            require(not path.is_symlink(), "SYMLINK")
            try:
                data = strict_json(path.read_text())
                state = data["payload"]
                require(data["sha256"] == digest(state), "CHECKSUM")
                require(state["schema"] == "t0-v1" and state["synthetic"] is True, "STATE_SCOPE")
            except (ValueError, KeyError, TypeError):
                damaged.append(path.name)
                continue
            if damaged:
                state["recovery_required"] = True
            return state, {"degraded": bool(damaged), "skipped": damaged, "snapshot": path.name}
        raise ContractError("NO_VALID_SNAPSHOT")


def begin_poll(state, live_id):
    require(state["runtime"] == "RUNNING", "NOT_RUNNING")
    sim_id(live_id)
    old = state["comments"].get(live_id, {})
    state["comments"][live_id] = {"ids": old.get("ids", []), "cursor": None, "seen_cursors": [],
                                  "coverage": "INCOMPLETE", "pages": 0, "order": None}


def resume_poll(state, live_id, store):
    require(state["runtime"] == "RUNNING", "NOT_RUNNING")
    candidate = deepcopy(state)
    poll = candidate["comments"][sim_id(live_id)]
    require(poll["coverage"] == "GAP" or (poll["coverage"] == "ERROR" and poll.get("error") in {"UNAVAILABLE", "RATE_LIMIT"}), "POLL_NOT_RESUMABLE")
    poll["coverage"] = "INCOMPLETE"
    poll["pages"] = 0
    poll.pop("error", None)
    commit(state, candidate, store)


def accept_page(state, live_id, page, now, store, page_budget=5):
    require(state["runtime"] == "RUNNING", "NOT_RUNNING")
    require(type(page_budget) is int and page_budget > 0, "PAGE_BUDGET")
    timestamp(now)
    candidate = deepcopy(state)
    poll = candidate["comments"][sim_id(live_id)]
    require(poll["coverage"] not in {"OBSERVED_WINDOW", "CURSOR_LOOP", "CURSOR_MISMATCH", "ERROR"}, "POLL_CLOSED")
    if poll["pages"] >= page_budget:
        poll["coverage"] = "GAP"
    elif page.get("error"):
        require(page["error"] in {"RATE_LIMIT", "PERMISSION", "UNAVAILABLE"}, "ERROR_CODE")
        poll["coverage"] = "ERROR"
        poll["error"] = page["error"]
    elif page.get("cursor") != poll["cursor"]:
        poll["coverage"] = "CURSOR_MISMATCH"
    else:
        require(page.get("order") in {"ranked", "reverse_chronological"}, "ORDER")
        next_cursor = page.get("next_cursor")
        if next_cursor is not None:
            sim_id(next_cursor)
        # Validate the whole page before committing either IDs or cursor.
        ids = set(poll["ids"])
        for item in page["items"]:
            ids.add(sim_id(item["id"]))
            timestamp(item["created_at"])
        poll["ids"] = sorted(ids)
        poll["pages"] += 1
        poll["order"] = page["order"]
        poll["seen_cursors"].append(page.get("cursor"))
        poll["cursor"] = next_cursor
        poll["coverage"] = "OBSERVED_WINDOW" if next_cursor is None else "INCOMPLETE"
        if next_cursor is not None and next_cursor in poll["seen_cursors"]:
            poll["coverage"] = "CURSOR_LOOP"
        elif next_cursor is not None and poll["pages"] >= page_budget:
            poll["coverage"] = "GAP"
        tick(candidate, now, "comments")
        record_event(candidate, "PAGE_SAVED", live_id)
    commit(state, candidate, store)
    return state["comments"][live_id]["coverage"]


def retry_delay(error, attempt, retry_after=None, max_attempts=3):
    require(type(attempt) is int and attempt >= 0, "ATTEMPT")
    require(error in {"RATE_LIMIT", "UNAVAILABLE", "PERMISSION", "AUTH"}, "ERROR_CODE")
    if error in {"AUTH", "PERMISSION"} or attempt >= max_attempts:
        return None
    if retry_after is not None:
        require(type(retry_after) is int and retry_after >= 0, "RETRY_AFTER")
        return retry_after if retry_after <= 300 else None
    return min(300, 2 ** attempt)


def propose(action_id, object_id, account_id, currency, before, after, evidence_id, evidence_at):
    for value in (action_id, object_id, account_id, evidence_id):
        sim_id(value)
    require(currency in {"USD", "EUR", "JPY"}, "CURRENCY")
    number(before)
    number(after)
    timestamp(evidence_at)
    plan = {"action_id": action_id, "kind": "SIM_SET_BUDGET", "object_id": object_id,
            "account_id": account_id, "currency": currency, "before": before, "after": after,
            "evidence_id": evidence_id, "evidence_at": evidence_at}
    return {"plan": plan, "plan_hash": digest(plan), "status": "PROPOSED"}


def approve(proposal, reviewer, expires_at, max_budget):
    require(proposal["status"] == "PROPOSED", "PROPOSAL_STATE")
    require(reviewer == "SIM_REVIEWER", "SIM_APPROVER")
    timestamp(expires_at)
    number(max_budget)
    return {"plan_hash": proposal["plan_hash"], "reviewer": reviewer, "expires_at": expires_at, "max_budget": max_budget}


def execute_sim(state, proposal, approval, now, store, failure=None):
    require(state["runtime"] == "RUNNING" and not state["recovery_required"], "EXECUTION_STOPPED")
    require(failure in {None, "BEFORE_COMMIT", "AFTER_COMMIT"}, "FAILURE_MODE")
    plan = proposal["plan"]
    require(proposal["plan_hash"] == digest(plan) == approval["plan_hash"], "PLAN_CHANGED")
    require(plan["kind"] == "SIM_SET_BUDGET" and approval["reviewer"] == "SIM_REVIEWER", "SIM_ONLY")
    for field in ("action_id", "object_id", "account_id", "evidence_id"):
        sim_id(plan[field])
    action_id = plan["action_id"]
    if action_id in state["actions"]:
        require(state["actions"][action_id]["plan_hash"] == proposal["plan_hash"], "ACTION_ID_CONFLICT")
        return state["actions"][action_id]["status"]
    require(timestamp(now) < timestamp(approval["expires_at"]), "APPROVAL_EXPIRED")
    require(freshness(plan["evidence_at"], now, 300) == "FRESH", "EVIDENCE_STALE")
    obj = state["sim_objects"][plan["object_id"]]
    require(obj["account_id"] == plan["account_id"] and obj["currency"] == plan["currency"], "OBJECT_CONTEXT")
    require(obj["status"] == "SIM_PAUSED" and number(obj["budget"]) == number(plan["before"]), "RECHECK_FAILED")
    require(number(plan["after"]) <= number(approval["max_budget"]), "BUDGET_LIMIT")
    candidate = deepcopy(state)
    candidate["actions"][action_id] = {"plan_hash": proposal["plan_hash"], "status": "UNKNOWN"}
    # Write-ahead unknown checkpoint: a crash never grants automatic re-execution.
    commit(state, candidate, store)
    completed = deepcopy(state)
    if failure != "BEFORE_COMMIT":
        completed["sim_objects"][plan["object_id"]]["budget"] = plan["after"]
        completed["receipts"][action_id] = {"plan_hash": proposal["plan_hash"], "object_id": plan["object_id"], "after": plan["after"]}
    if failure:
        record_event(completed, "ACTION_UNKNOWN", action_id)
    else:
        require(completed["sim_objects"][plan["object_id"]]["budget"] == plan["after"], "READBACK_FAILED")
        completed["actions"][action_id]["status"] = "VERIFIED_SIM"
        record_event(completed, "ACTION_VERIFIED", action_id)
    try:
        commit(state, completed, store)
    except OSError:
        state["recovery_required"] = True
        raise
    return state["actions"][action_id]["status"]


def reconcile(state, proposal, store):
    plan = proposal["plan"]
    require(digest(plan) == proposal["plan_hash"], "PLAN_CHANGED")
    action = state["actions"].get(plan["action_id"])
    require(action is not None and action["plan_hash"] == proposal["plan_hash"], "ACTION_MISSING")
    receipt = state["receipts"].get(plan["action_id"])
    expected = {"plan_hash": proposal["plan_hash"], "object_id": plan["object_id"], "after": plan["after"]}
    if receipt == expected and state["sim_objects"][plan["object_id"]]["budget"] == plan["after"]:
        candidate = deepcopy(state)
        candidate["actions"][plan["action_id"]]["status"] = "VERIFIED_SIM"
        commit(state, candidate, store)
    return state["actions"][plan["action_id"]]["status"]


def anomaly(current, previous, threshold_fraction, source_freshness):
    require(Decimal(0) < number(threshold_fraction) < 1, "THRESHOLD")
    if source_freshness != "FRESH" or current is None or previous is None:
        return "INSUFFICIENT_DATA"
    old, new = number(previous), number(current)
    if old == 0:
        return "BASELINE_ZERO"
    return "REVIEW_DROP" if (old - new) / old >= number(threshold_fraction) else "NO_RULE_TRIGGER"
