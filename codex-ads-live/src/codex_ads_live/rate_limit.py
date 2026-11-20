"""Reduce provider headers to a bounded cooldown without retaining raw metadata."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re

from .validation import InputError, aware_time, fail, object_only, strict_json


BASES = frozenset({'RETRY_AFTER', 'META_BUC_ESTIMATE', 'LOCAL_FALLBACK'})
NO_HINT_WAIT_SECONDS = 60
MAX_AUTOMATIC_WAIT_SECONDS = 30


def _after(raw, observed):
    if type(raw) is not str or len(raw)>128:
        return None
    value = raw.strip()
    if re.fullmatch(r'[0-9]+',value):
        return _future(observed,int(value))
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is not None:
            return date.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _business_estimate(raw, observed):
    if type(raw) is not str or len(raw)>16384:
        return None
    try:
        body = strict_json(raw)
    except ValueError:
        return None
    if type(body) is not dict or len(body)>32:
        return None
    minutes = 0
    for rows in body.values():
        if type(rows) is not list or len(rows)>32:
            continue
        for row in rows:
            value = row.get('estimated_time_to_regain_access') if type(row) is dict else None
            if type(value) is int and value>0:
                minutes = max(minutes,value)
    return _future(observed,60*minutes) if minutes else None


def _future(observed, seconds):
    try:
        return observed+timedelta(seconds=seconds)
    except OverflowError:
        return datetime.max.replace(tzinfo=timezone.utc)


def resume_time(hold):
    return aware_time(hold.get('local_not_before',hold['not_before']))


def validate_hold(hold, requests_reserved, error):
    fields = ('observed_at','not_before','basis','automatic_retries_reserved')
    try:
        object_only(hold,(*fields,'local_not_before'),fields)
        observed = aware_time(hold['observed_at'])
        not_before = aware_time(hold['not_before'])
        effective = resume_time(hold)
    except InputError:
        fail(error)
    basis, retries = hold['basis'], hold['automatic_retries_reserved']
    if (type(basis) is not str or basis not in BASES or not_before<=observed
            or ('local_not_before' in hold and effective<=not_before)
            or type(requests_reserved) is not int or type(retries) is not int
            or not 0<=retries<=min(1,requests_reserved)):
        fail(error)


def read_archived_hold(report, result, requests_reserved, error):
    hold = report.get('rate_limit')
    pending = report.get('rate_limit_pending')
    if (hold!=result.get('rate_limit') or pending is not result.get('rate_limit_pending')
            or (pending is not None if hold is None else type(pending) is not bool)):
        fail(error)
    if hold is not None:
        validate_hold(hold,requests_reserved,error)
    return hold


def preserve_remaining_wait(hold, observed, remaining_seconds):
    local = _future(observed,remaining_seconds)
    if local <= resume_time(hold):
        return hold
    return dict(hold,local_not_before=local.isoformat())


def cooldown(response, observed):
    """Retain the latest indicated time, with at least a second before retry."""
    observed = observed.astimezone(timezone.utc)
    dates = [(_after(response.getheader('Retry-After'),observed),'RETRY_AFTER'),
             (_business_estimate(response.getheader('X-Business-Use-Case-Usage'),observed),'META_BUC_ESTIMATE')]
    hints = [(date,basis) for date,basis in dates if date is not None]
    if not hints:
        hints = [(_future(observed,NO_HINT_WAIT_SECONDS),'LOCAL_FALLBACK')]
    resume_at, basis = max(hints,key=lambda hint:hint[0])
    return dict(observed_at=observed.isoformat(),not_before=max(_future(observed,1),resume_at).isoformat(),basis=basis)
