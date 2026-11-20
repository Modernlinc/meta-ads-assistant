"""Evaluate explicit account-level review thresholds on comparable saved windows."""
from decimal import Decimal
from fractions import Fraction
import re

from .journal import digest
from .local_paths import checked_path
from .period_review import RATIO_INPUTS
from .read_adapter import identifier, numeric_string
from .validation import fail, object_only, strict_json


SCHEMA = 'ads-period-alert-rules-v1'
SAMPLES = {'spend':'spend', 'cpm':'impressions', 'ctr_percent':'impressions',
           'website_purchase_cpa':'website_purchases', 'website_purchase_roas':'website_purchases',
           'website_lead_cpl':'website_leads'}
DIRECTIONS = ('INCREASE','DECREASE','EITHER')
RULE_KEYS = ('metric','direction','threshold_percent','minimum_per_period')
POLICY_KEYS = ('schema','account_id','currency','minimum_lag_days','rules')


def validate_rules(policy):
    object_only(policy,POLICY_KEYS,POLICY_KEYS)
    if policy['schema']!=SCHEMA: fail('PERIOD_ALERT_SCHEMA')
    identifier(policy['account_id'])
    if type(policy['currency']) is not str or not re.fullmatch('[A-Z]{3}',policy['currency']):
        fail('PERIOD_ALERT_CURRENCY')
    lag = policy['minimum_lag_days']
    if type(lag) is not int or lag < 1: fail('PERIOD_ALERT_LAG_DAYS')
    rules = policy['rules']
    if type(rules) is not list or not 1<=len(rules)<=len(SAMPLES): fail('PERIOD_ALERT_RULES')
    seen = set()
    for rule in rules:
        object_only(rule,RULE_KEYS,RULE_KEYS)
        metric = rule['metric']
        if type(metric) is not str or metric not in SAMPLES or metric in seen: fail('PERIOD_ALERT_METRIC')
        seen.add(metric)
        if rule['direction'] not in DIRECTIONS: fail('PERIOD_ALERT_DIRECTION')
        threshold = Decimal(numeric_string(rule['threshold_percent']))
        minimum = Decimal(numeric_string(rule['minimum_per_period'],integer=metric!='spend'))
        if threshold<=0 or (metric!='spend' and minimum<=0): fail('PERIOD_ALERT_THRESHOLD')
    return policy


def load_rules(path):
    file = checked_path(path,link_error='PERIOD_ALERT_FILE_LINK',volume_error='PERIOD_ALERT_FILE_VOLUME')
    if not file.is_file() or file.stat().st_size>16000: fail('PERIOD_ALERT_FILE')
    with file.open('rb') as handle:
        raw = handle.read(16001)
    if len(raw)>16000: fail('PERIOD_ALERT_FILE')
    return validate_rules(strict_json(raw.decode('utf-8')))


def pending(policy, reason):
    checks = [dict(rule,sample_metric=SAMPLES[rule['metric']],sample_values=None,status='NOT_EVALUATED',reason=reason)
              for rule in policy['rules']]
    return dict(state='NOT_EVALUATED',reason=reason,rules_sha256=digest(policy),
                rule_scope={key:policy[key] for key in ('account_id','currency')},
                minimum_lag_days=policy['minimum_lag_days'],checks=checks,review_signals=0,causal_claim_allowed=False,
                remote_write_authorized=False)


def exact_value(metrics, name):
    if name not in RATIO_INPUTS:
        return Fraction(Decimal(metrics[name]['value']))
    numerator, denominator, factor = RATIO_INPUTS[name]
    return Fraction(Decimal(metrics[numerator]['value']))*factor/Fraction(Decimal(metrics[denominator]['value']))


def evaluate_rule(row, previous, current, changes):
    metric = row['metric']
    sample = row['sample_metric']
    if metric not in changes or sample not in changes:
        return dict(row,reason='METRIC_NOT_REQUESTED')
    pair = {label:values[sample]['value'] for label,values in (('previous',previous),('current',current))}
    row = dict(row,sample_values=pair)
    if None in pair.values(): return dict(row,reason='SAMPLE_VALUE_UNAVAILABLE')
    if min(map(Decimal,pair.values())) < Decimal(row['minimum_per_period']):
        return dict(row,reason='SAMPLE_BELOW_DECLARED_MINIMUM')
    change = changes[metric]
    if change['previous'] is None or change['current'] is None: return dict(row,reason='METRIC_VALUE_UNAVAILABLE')
    baseline = exact_value(previous,metric)
    if not baseline: return dict(row,reason='ZERO_BASELINE')
    delta = (exact_value(current,metric) / baseline - 1)*100
    threshold = Fraction(Decimal(row['threshold_percent']))
    observed = {'EITHER':abs(delta),'INCREASE':delta,'DECREASE':-delta}[row['direction']]
    return dict(row,status='REVIEW_SIGNAL' if observed>=threshold else 'WITHIN_DECLARED_RULE',
                reason=None,observed_change_percent=change['change_percent'],threshold_uses_unrounded_values=True)


def evaluate(policy, report, previous, current, *, scope_verified):
    context = report['periods']['current']['context']
    if (policy['account_id'],policy['currency']) != (context['account_id'],context['currency']):
        return pending(policy,'RULE_ACCOUNT_OR_CURRENCY_MISMATCH')
    if not scope_verified: return pending(policy,'WORKSPACE_SCOPE_UNVERIFIED')
    if not report['same_weekday_mix']: return pending(policy,'WEEKDAY_MIX_DIFFERS')
    lag = min(row['calendar_days_after_period_end_at_observation'] for row in report['periods'].values())
    if lag < policy['minimum_lag_days']: return pending(policy,'MINIMUM_DECLARED_LAG_NOT_MET')
    result = pending(policy,None)
    checks = [evaluate_rule(row,previous,current,report['account_metrics']) for row in result['checks']]
    states = {row['status'] for row in checks}
    result.update(checks=checks,review_signals=sum(row['status']=='REVIEW_SIGNAL' for row in checks),
                  state='NOT_EVALUATED' if states=={'NOT_EVALUATED'} else
                        'PARTIALLY_EVALUATED' if 'NOT_EVALUATED' in states else 'EVALUATED')
    return result


def schema():
    def obj(properties):
        return dict(type='object',additionalProperties=False,required=list(properties),properties=properties)
    numeric = dict(type='string',pattern=r'^[0-9]{1,24}(?:\.[0-9]{1,12})?$')
    rule = obj(dict(metric={'enum':list(SAMPLES)},direction={'enum':list(DIRECTIONS)},threshold_percent=numeric,
                    minimum_per_period=numeric))
    policy = obj(dict(schema={'const':SCHEMA},account_id={'type':'string','pattern':'^(?:[0-9]{1,40}|SIM_[A-Z0-9_]{1,60})$'},
                      currency={'type':'string','pattern':'^[A-Z]{3}$'},minimum_lag_days={'type':'integer','minimum':1},
                      rules={'type':'array','minItems':1,'maxItems':len(SAMPLES),'items':rule}))
    return {'$schema':'https://json-schema.org/draft/2020-12/schema','title':'Local account-level review thresholds',**policy,
            '$comment':'Unique metric per rule file, positive percentage, and positive integer sample for ratios are enforced at runtime. Spend accepts a zero floor. Count thresholds are for returned platform events or impressions, not verified business outcomes.'}
