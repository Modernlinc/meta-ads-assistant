"""Bounded, restartable monitoring of local commerce export files."""
from datetime import timedelta, datetime
from pathlib import Path
from time import monotonic, sleep

from .commerce_csv import reconcile
from .journal import Journal, digest
from .local_paths import checked_path
from .meta_write_mapping import utcnow
from .operator_review import load_input, minor
from .timezones import load_zone
from .validation import InputError, aware_time, fail, object_only


def dir_path(value):
    return checked_path(value, link_error="WATCH_PATH_LINK", volume_error="WATCH_VOLUME_NOT_MOUNTED")


def validate_control(value):
    object_only(value, ("generation", "requested_at"), ("generation", "requested_at"))
    if type(value["generation"]) is not int or not 1 <= value["generation"] < 10**9:
        fail("WATCH_CONTROL_GENERATION")
    aware_time(value["requested_at"])


def control(root, new=False, *, initialize=False):
    marker = root / "control"
    existing_only = not (new or initialize)
    if not marker.exists() and not marker.is_symlink() and existing_only:
        return 0
    journal = Journal(marker, mode="READ_ONLY", existing_only=existing_only, stage_snapshots=True)
    with journal.locked(wait_seconds=0.5, existing_only=existing_only):
        value, damaged = journal.load(validate_control)
        if damaged or journal.unpublished_snapshot_count():
            fail("WATCH_CONTROL_NEEDS_REVIEW")
        current = value["generation"] if value else 0
        if new:
            current += 1
            journal.save(dict(generation=current, requested_at=utcnow().isoformat()), validate_control)
        return current


def validate_state(value):
    fields = ("config_sha256", "acknowledged_stop", "cycles", "successful_at", "last_checked_at", "report", "last_error")
    object_only(value, (*fields, "alerts", "fresh_for_seconds"), fields)
    if "alerts" in value and (type(value["alerts"]) is not list or len(value["alerts"]) > 16):
        fail("WATCH_ALERT_STATE")
    if "fresh_for_seconds" in value and not 1 <= (minor(value["fresh_for_seconds"]) or 0) <= 604800:
        fail("WATCH_FRESHNESS_INVALID")
    if type(value["cycles"]) is not int or not 0 <= value["cycles"] <= 10**7:
        fail("WATCH_CYCLE_STATE")
    if type(value["acknowledged_stop"]) is not int or value["acknowledged_stop"] < 0:
        fail("WATCH_CONTROL_GENERATION")
    for key in ("successful_at", "last_checked_at"):
        if value[key] is not None:
            aware_time(value[key])


def watch_config(input_path):
    config = load_input(input_path)
    required = ("schema", "store_id", "account_id", "currency", "orders", "refunds", "spend", "refund_basis")
    windows = ("since", "until", "days", "timezone")
    object_only(config, (*required, *windows, "fresh_for_seconds", "spend_alert_minor"), required)
    if config["schema"] != "shop-commerce-watch-v1":
        fail("WATCH_CONFIG_SCHEMA")
    fixed, rolling = {"since", "until"}, {"days", "timezone"}
    if (set(config) & set(windows)) not in (fixed, rolling):
        fail("WATCH_PERIOD_SCOPE")
    if "days" in config and config["days"] not in (1, 7):
        fail("WATCH_DAYS_REQUIRED")
    if "fresh_for_seconds" in config and not 1 <= (minor(config["fresh_for_seconds"]) or 0) <= 604800:
        fail("WATCH_FRESHNESS_INVALID")
    minor(config.get("spend_alert_minor"))
    parent = dir_path(input_path).parent
    files = []
    for key in ("orders", "refunds", "spend"):
        name = config[key]
        if type(name) is not str or name != Path(name).name or not name.lower().endswith(".csv"):
            fail("WATCH_INPUT_FILENAME")
        files.append(parent / name)
    return config, files


def window(config):
    if "days" not in config:
        return config["since"], config["until"]
    try:
        zone = load_zone(config["timezone"])
    except (TypeError, ValueError, KeyError):
        fail("WATCH_ACCOUNT_TIMEZONE_REQUIRED")
    yesterday = datetime.now(zone).date() - timedelta(days=1)
    return (yesterday - timedelta(days=config["days"] - 1)).isoformat(), yesterday.isoformat()


def _open_root(directory, *, existing_only):
    root = dir_path(directory)
    return root, Journal(root / "runs", mode="READ_ONLY", existing_only=existing_only, stage_snapshots=True)


def stop(directory):
    root, _ = _open_root(directory, existing_only=True)
    return dict(status="STOP_REQUEST_RECORDED", control_generation=control(root, new=True))


def report(directory):
    root, journal = _open_root(directory, existing_only=True)
    with journal.locked(existing_only=True):
        saved, damaged = journal.load(validate_state)
        if damaged or journal.unpublished_snapshot_count() or saved is None:
            fail("WATCH_STATE_NEEDS_REVIEW")
        return _result(saved, 0, "STOP_REQUESTED" if control(root) > saved["acknowledged_stop"] else "REPORT_AVAILABLE")


def _result(saved, completed, status):
    stamp = saved["successful_at"]
    seconds = max(0, int((utcnow() - aware_time(stamp)).total_seconds())) if stamp else None
    freshness = ("NO_SUCCESSFUL_REPORT" if seconds is None else
                 "STALE" if seconds > saved.get("fresh_for_seconds", 86400) else "RECENT")
    alerts = list(saved.get("alerts", []))
    if freshness == "STALE":
        alerts.append("REPORT_STALE")
    if saved["last_error"] is not None:
        alerts.append("LATEST_READ_FAILED")
    return dict(status=status, completed_cycles=completed, total_cycles=saved["cycles"], report=saved["report"],
                last_error=saved["last_error"], last_success_at=stamp, report_age_seconds=seconds,
                freshness=freshness, alerts=alerts, lease="EXCLUSIVE_LOCAL_PROCESS_LOCK",
                schedule="FOREGROUND_ONLY", local_only=True, writes_to_platform=False)


def report_alerts(report, threshold):
    totals = report["period_totals"]
    alerts = []
    if totals["spend_missing_days"]:
        alerts.append("SPEND_SOURCE_PARTIAL")
    if totals["missing_cost_lines"]:
        alerts.append("ORDER_COST_MISSING")
    if threshold is not None and totals["observed_spend_minor"] > threshold:
        alerts.append("OBSERVED_SPEND_BOUND_EXCEEDED")
    return alerts


def run(input_path, directory, *, cycles, interval_seconds, runtime_seconds, retry_count, resume=False):
    if not (1 <= cycles <= 48 and 1 <= interval_seconds <= 3600 and 1 <= runtime_seconds <= 7200
            and 0 <= retry_count <= 3):
        fail("WATCH_BUDGET")
    config, files = watch_config(input_path)
    fingerprint = digest(dict(config=config, input_directory=str(dir_path(input_path).parent)))
    root, journal = _open_root(directory, existing_only=False)
    with journal.locked(wait_seconds=0.5):
        saved, damaged = journal.load(validate_state)
        if damaged or journal.unpublished_snapshot_count():
            fail("WATCH_STATE_NEEDS_REVIEW")
        if saved is None:
            saved = dict(config_sha256=fingerprint, acknowledged_stop=0, cycles=0, successful_at=None,
                         last_checked_at=None, report=None, last_error=None)
        elif saved["config_sha256"] != fingerprint:
            fail("WATCH_CONFIG_CHANGED_USE_NEW_STATE")
        # Create the lock before entering a read/retry cycle. A first stop
        # request can then contend on the same lock instead of creating it mid-read.
        generation = control(root, initialize=True)
        if resume:
            saved["acknowledged_stop"] = generation
            journal.save(saved, validate_state)
        if generation > saved["acknowledged_stop"]:
            return _result(saved, 0, "STOP_REQUESTED")
        deadline = monotonic() + runtime_seconds

        def pause(stop_at):
            while monotonic() < stop_at:
                if control(root) > generation:
                    return True
                delay = max(0, min(0.5, stop_at - monotonic()))
                if delay:
                    sleep(delay)
            return False

        for completed in range(cycles):
            for attempt in range(retry_count + 1):
                if control(root) > generation:
                    return _result(saved, completed, "STOP_REQUESTED")
                if monotonic() >= deadline:
                    return _result(saved, completed, "TIME_BUDGET_REACHED")
                since, until = window(config)
                try:
                    saved["report"] = reconcile(*files, config["store_id"], config["account_id"],
                                                config["currency"], since, until, config["refund_basis"])
                except (InputError, OSError, UnicodeError) as error:
                    saved["last_error"] = str(error) if isinstance(error, InputError) else "WATCH_LOCAL_READ_FAILED"
                    saved["last_checked_at"] = utcnow().isoformat()
                    journal.save(saved, validate_state)
                    if attempt < retry_count and pause(min(deadline, monotonic() + 2**attempt)):
                        return _result(saved, completed, "STOP_REQUESTED")
                else:
                    saved.update(last_error=None, successful_at=utcnow().isoformat(),
                                 fresh_for_seconds=config.get("fresh_for_seconds", 86400),
                                 alerts=report_alerts(saved["report"], config.get("spend_alert_minor")))
                    break
            saved["cycles"] += 1
            saved["last_checked_at"] = utcnow().isoformat()
            journal.save(saved, validate_state)
            if completed + 1 < cycles and pause(min(deadline, monotonic() + interval_seconds)):
                return _result(saved, completed + 1, "STOP_REQUESTED")
        return _result(saved, cycles, "CYCLE_BUDGET_REACHED")
