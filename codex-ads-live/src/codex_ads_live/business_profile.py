"""Declared business inputs for one account's local analysis and proposals."""
from decimal import Decimal
import re

from .journal import digest
from .validation import fail, object_only


SCHEMA = 'workspace-business-profile-v1'
EVENTS = ('PURCHASE', 'LEAD', 'QUALIFIED_LEAD', 'OTHER')
CLIENT_ID = r'client_[0-9a-f]{32}'
ACCOUNT_ID = r'[1-9][0-9]{0,39}'
MONEY = r'[0-9]{1,9}(?:\.[0-9]{1,3})?'
EVIDENCE_REF = r'EV_[A-Z0-9_]{1,80}'
SECRET_PATTERN = re.compile(
    r'EAA[A-Za-z0-9]{40,}|\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|PRIVATE KEY'
    r'|(?:access[_ -]?token|app[_ -]?secret|cookie|password|authorization)\s*[:=]'
    r'|\bbearer\s+[A-Za-z0-9._~-]{12,}', re.I)
TEXT_LIMITS = {'offer_summary': 240, 'goal': 240, 'result_definition': 300}
INTEGER_LIMITS = {'observation_days': 90, 'minimum_results': 100000}
MONEY_FIELDS = ('target_cost_per_result', 'maximum_test_spend', 'protected_daily_spend')
OPTIONAL_VALUES = ('baseline_evidence_ref', *MONEY_FIELDS, *INTEGER_LIMITS)


def blank(client_id, account_id):
    return dict(schema=SCHEMA, client_id=client_id, account_id=account_id,
                offer_summary=None, goal=None, result_type=None, result_definition=None,
                baseline_evidence_ref=None, currency=None, target_cost_per_result=None,
                maximum_test_spend=None, protected_daily_spend=None, observation_days=None,
                minimum_results=None)


def validate(value):
    keys = blank(None, None)
    object_only(value, keys, keys)
    if value['schema'] != SCHEMA:
        fail('BUSINESS_PROFILE_SCHEMA')
    patterns = {
        'client_id': CLIENT_ID, 'account_id': ACCOUNT_ID, 'currency': r'[A-Z]{3}',
        'baseline_evidence_ref': EVIDENCE_REF,
        **dict.fromkeys(MONEY_FIELDS, MONEY),
    }
    for key, pattern in patterns.items():
        field = value[key]
        if field is None and key in OPTIONAL_VALUES:
            continue
        if type(field) is not str or not re.fullmatch(pattern, field):
            fail('BUSINESS_PROFILE_FIELD_FORMAT')
    if value['result_type'] not in EVENTS:
        fail('BUSINESS_PROFILE_RESULT_TYPE')
    for key, limit in TEXT_LIMITS.items():
        field = value[key]
        if (type(field) is not str or not 1 <= len(field) <= limit
                or field != field.strip() or any(not char.isprintable() for char in field)):
            fail('BUSINESS_PROFILE_TEXT_FORMAT')
        if (SECRET_PATTERN.search(field) or re.search(r'\bhttps?://|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', field, re.I)):
            fail('BUSINESS_PROFILE_PRIVATE_TEXT')
    target = value['target_cost_per_result']
    if target is not None and Decimal(target) <= 0:
        fail('BUSINESS_PROFILE_POSITIVE_COST_REQUIRED')
    for key, limit in INTEGER_LIMITS.items():
        field = value[key]
        if field is not None and (type(field) is not int or not 1 <= field <= limit):
            fail('BUSINESS_PROFILE_INTEGER_RANGE')
    return value


def describe(value, observed_currency):
    if value is None:
        return {'status': 'BUSINESS_PROFILE_NOT_CONFIGURED'}
    maximum = value['maximum_test_spend']
    return dict(status='DECLARED_BUSINESS_PROFILE',
                observed_currency=observed_currency,
                currency_check=(
                    'UNCHECKED' if observed_currency is None else
                    'MATCH' if observed_currency == value['currency'] else 'MISMATCH'
                ),
                missing_decision_inputs=[key for key in OPTIONAL_VALUES if value[key] is None],
                zero_test_spend_declared=maximum is not None and Decimal(maximum) == 0,
                profile_sha256=digest(value), profile=value)


def schema():
    def pattern(value):
        return {'type': 'string', 'pattern': '^'+value+'$'}
    fields = dict(schema={'const': SCHEMA}, client_id=pattern(CLIENT_ID), account_id=pattern(ACCOUNT_ID),
                  currency=pattern('[A-Z]{3}'), result_type={'enum': list(EVENTS)},
                  baseline_evidence_ref=pattern(EVIDENCE_REF))
    fields.update({key: {'type': 'string', 'minLength': 1, 'maxLength': limit}
                   for key, limit in TEXT_LIMITS.items()})
    fields.update({key: {'type': 'integer', 'minimum': 1, 'maximum': limit}
                   for key, limit in INTEGER_LIMITS.items()})
    fields.update(dict.fromkeys(MONEY_FIELDS, pattern(MONEY)))
    for key in OPTIONAL_VALUES:
        fields[key] = {'anyOf': [fields[key], {'type': 'null'}]}
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'Declared business inputs for one account',
            'type': 'object', 'required': list(fields), 'additionalProperties': False, 'properties': fields,
            '$comment': 'Major currency units. Runtime also checks text safety, positive declared result cost, and exact client/account mapping. Evidence reference is an opaque alias. Blank preparation is intentionally incomplete.'}
