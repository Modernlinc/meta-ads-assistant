"""Strict, non-echoing validation shared by local commands."""
from datetime import datetime
import json
import math
import re
from urllib.parse import parse_qsl, urlsplit


class InputError(ValueError):
    """Errors use fixed codes; never include user-supplied content."""


def fail(code):
    raise InputError(code)


def object_only(value, allowed, required=()):
    if type(value) is not dict:
        fail("EXPECTED_OBJECT")
    if not set(value) <= set(allowed):
        fail("UNRECOGNIZED_FIELD")
    if not set(required) <= set(value):
        fail("REQUIRED_FIELD_MISSING")


def aware_time(value):
    if type(value) is not str or len(value) > 40:
        fail("INVALID_TIMESTAMP")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            fail("TIMEZONE_REQUIRED")
        return parsed
    except (ValueError, OverflowError):
        fail("INVALID_TIMESTAMP")


def strict_json(text, *, max_chars=1_000_000):
    if len(text) > max_chars:
        fail("INPUT_TOO_LARGE")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail("DUPLICATE_FIELD")
            result[key] = value
        return result

    def constant(_):
        fail("NON_FINITE_NUMBER")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, RecursionError):
        fail("INVALID_JSON")


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_https_destination(value, error):
    if type(value) is not str:
        fail(error)
    try:
        url = urlsplit(value)
        _ = url.port
    except ValueError:
        fail(error)
    if (url.scheme != 'https' or not url.hostname or url.username is not None
            or url.password is not None or url.fragment
            or any(re.search(r'token|secret|password|signature|credential|cookie|authorization|x-amz', key, re.I)
                   for key, _ in parse_qsl(url.query))):
        fail(error)


def digest_scan_view(value, fields):
    """Keep free text visible to scanning; mask only exact digests at known keys."""
    if type(value) is not dict:
        return value
    view = value.copy()
    for key in fields:
        candidate = view.get(key)
        if type(candidate) is str and re.fullmatch(r'[0-9a-f]{64}', candidate):
            view[key] = '<sha256>'
    return view
