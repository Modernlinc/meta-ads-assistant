"""One local operator case from intake to the original, independently approved Ad."""
from decimal import Decimal
from pathlib import Path

from .client_workspace import asset_key, draft_review_scope, journal_for, load, workspace_identity
from .creative_research import freeze_selection
from .first_launch_plan import COMPATIBLE_PROFILE_VERSIONS, current_plan, prepare
from .first_launch_progress import progress
from .journal import Journal, digest
from .operator_review import business_readiness, load_input, minor, pattern, strategy_review, structured, tracking_audit
from .validation import fail, object_only


def add_command(subparsers):
    cmd = subparsers.add_parser('media-buyer', help='在同一工作区接续广告准备、首投和同一广告复盘')
    cmd.add_argument('phase', choices=('prepare', 'plan', 'status', 'review', 'optimize', 'report', 'learn'))
    cmd.add_argument('--workspace', required=True, type=Path)
    cmd.add_argument('--case', required=True)
    cmd.add_argument('--input', type=Path, help='prepare 使用准备材料，review 使用具名复盘范围，optimize 使用提案材料')
    cmd.add_argument('--output', type=Path, help='report 输出同一复盘的 XLSX 和 Markdown')


def _minor_money(value, currency):
    if value is None:
        return None
    scale = Decimal(value).scaleb(0 if currency == 'JPY' else 2)
    if scale != scale.to_integral_value():
        fail('BUYER_WORKSPACE_LIMIT_MINOR_UNITS')
    return int(scale)


def _intake(doc, workspace):
    structured(doc, 'media-buyer-intake-v1', ('page_id', 'business', 'tracking', 'strategy'),
               ('creatives', 'creative_choices', 'partner_briefs', 'competitor', 'method_receipts',
                'selected_strategy', 'creative_mode', 'catalog_review', 'policy_review', 'existing_structure',
                'tracking_configuration'))
    page = pattern(doc['page_id'], r'[1-9][0-9]{0,39}')
    business = business_readiness(doc['business'])
    tracking = tracking_audit(doc['tracking'])
    strategy = strategy_review(doc['strategy'])
    account, currency = business['account_id'], business['currency']
    facts = business['fields']
    raw = doc['strategy']
    destination = facts['DESTINATION']['value']
    if (strategy['account_id'], strategy['currency']) != (account, currency):
        fail('BUYER_INPUT_ACCOUNT_OR_CURRENCY_MISMATCH')
    if ((destination is not None and destination != doc['tracking']['destination'])
            or raw['destination'] != doc['tracking']['destination']
            or any(facts[key]['value'] is not None and facts[key]['value'] != raw[field]
                   for key, field in (('PRODUCT', 'product'), ('OFFER', 'offer')))
            or any(facts[key.upper()]['value'] != raw['commercial'][key]
                   for key in ('net_order_minor', 'variable_cost_minor'))):
        fail('BUYER_INPUT_FACTS_DISAGREE')
    creatives = doc.get('creatives', [])
    choice_files = doc.get('creative_choices', [])
    creative_mode = doc.get('creative_mode', 'MANUAL')
    if creative_mode not in ('MANUAL', 'CATALOG_DYNAMIC'):
        fail('BUYER_UNSUPPORTED_CREATIVE_MODE')
    catalog_mode = creative_mode == 'CATALOG_DYNAMIC'
    selected_strategy = doc.get('selected_strategy')
    sequence = selected_strategy.get('sequence') if type(selected_strategy) is dict else None
    structure_first = sequence == 'STRUCTURE_FIRST'
    minimum, maximum = (0, 0) if structure_first or catalog_mode else (1, 6)
    if catalog_mode and (creatives or choice_files):
        fail('BUYER_CATALOG_MEDIA_MODE_CONFLICT')
    if (type(creatives) is not list or type(choice_files) is not list
            or not minimum <= len(creatives) + len(choice_files) <= maximum
            or any(type(path) is not str or not path for path in choice_files)):
        fail('BUYER_CREATIVE_COUNT')
    selection = []
    for row in creatives:
        keys = ('concept_id', 'variant_id', 'asset_sha256')
        object_only(row, keys, keys)
        selection.append((row['concept_id'], row['variant_id'], pattern(row['asset_sha256'], r'[a-f0-9]{64}')))
    from .partner_brief import review as review_brief

    briefs = doc.get('partner_briefs', [])
    if not isinstance(briefs, list) or len(briefs) > 14:
        fail('BUYER_BRIEF_LIMIT')
    briefs = [review_brief(item) for item in briefs]
    method_receipts = doc.get('method_receipts', [])
    if type(method_receipts) is not list or len(briefs) + len(method_receipts) > 18:
        fail('BUYER_BRIEF_LIMIT')
    if any(type(path) is not str or not path for path in method_receipts):
        fail('BUYER_METHOD_RECEIPT_PATH')

    competitor = None
    if 'competitor' in doc:
        from .ads_library import inspect_detail

        item = doc['competitor']
        keys = ('observation', 'page_id', 'archive_id')
        object_only(item, (*keys, 'media_report', 'angle'), keys)
        competitor = inspect_detail(item['observation'], item['page_id'], item['archive_id'],
                                    media=item.get('media_report'), angle=item.get('angle'))
    with workspace.locked(existing_only=True):
        profile = load(workspace)
        scope = draft_review_scope(profile, account, compatible_versions=COMPATIBLE_PROFILE_VERSIONS,host_draft=True)
        if asset_key('PAGE', page) not in profile['assets']:
            fail('BUYER_PAGE_NOT_MAPPED')
        from .creative_research import selection_from_file

        selection.extend(selection_from_file(workspace, profile, account, path) for path in choice_files)
        if len({item[2] for item in selection}) != len(selection):
            fail('BUYER_CREATIVE_DUPLICATE')
        frozen = [freeze_selection(workspace, profile, account, *item) for item in selection]
    if method_receipts:
        from .method_handoff import load_receipt
        briefs.extend(load_receipt(path, scope['workspace_identity'], account) for path in method_receipts)
    if any((item['product'], item['market']) != (raw['product'], business['market']) for item in briefs):
        fail('BUYER_BRIEF_PRODUCT_OR_MARKET_MISMATCH')
    registered = scope['business_profile'] or {}
    limits = {key: _minor_money(registered.get(key), currency)
              for key in ('maximum_test_spend', 'protected_daily_spend')}
    context_currency = scope['account_context'].get('currency')
    if ((registered and registered['currency'] != currency)
            or (context_currency is not None and context_currency != currency)
            or (registered and registered['result_type'] != raw['goal'])):
        fail('BUYER_WORKSPACE_GOAL_OR_CURRENCY_MISMATCH')
    blockers = []
    if not profile['app_id'] or not profile['business_id']:
        blockers.append('CONTINUE_CREATION_IN_AUTHORIZED_HOST_OR_BIND_OWN_APP')
    catalog = load_input(doc['catalog_review']) if 'catalog_review' in doc else None
    policy = load_input(doc['policy_review']) if 'policy_review' in doc else None
    if catalog is not None:
        if (type(catalog) is not dict or catalog.get('source_scope') != 'MERCHANT_SUPPLIED_FEED_AND_EVENT_ID_SAMPLE'
                or catalog.get('currency') != currency or catalog.get('actions_authorized') is not False):
            fail('BUYER_CATALOG_REVIEW_SCOPE')
    if creative_mode == 'CATALOG_DYNAMIC':
        if catalog is None or catalog.get('status') != 'CATALOG_EXPORT_PREFLIGHT_READY':
            blockers.append('COMPLETE_CATALOG_FEED_PRODUCT_SET_AND_EVENT_PREFLIGHT')
        template = catalog.get('template_review') if catalog is not None else None
        if template is not None:
            try:
                fields = template['request_fields']['object_story_spec']
                valid = (fields['page_id'] == page and fields['template_data']['link'] == destination
                         and template['dispatchable'] is False)
            except (KeyError,TypeError):
                valid = False
            if not valid:
                fail('BUYER_CATALOG_TEMPLATE_SCOPE')
        blockers.append('CATALOG_DYNAMIC_REQUIRES_PLATFORM_SPECIFIC_FLOW')
    if policy is not None:
        if type(policy) is not dict or policy.get('status') != 'POLICY_PREFLIGHT_PREPARED' or policy.get('ad_id') is not None:
            fail('BUYER_POLICY_REVIEW_SCOPE')
        if policy.get('special_category') != 'NONE_REVIEWED':
            blockers.append('CONFIRM_SPECIAL_CATEGORY_AND_PLACEMENT_SCOPE')
    if business['status'] != 'BUSINESS_EVIDENCE_READY':
        blockers.append('COMPLETE_PRODUCT_OFFER_COST_OR_LANDING_EVIDENCE')
    if (not tracking['utm_keys'] or tracking['duplicate_utm_keys']
            or tracking['missing_event_ids'] or tracking['event_id_used_with_multiple_names']
            or tracking['observed_events'] == 0):
        blockers.append('REVIEW_TRACKING_EVIDENCE')
    if 'tracking_configuration' in doc:
        from .tracking_setup import consume_configuration

        if type(doc['tracking_configuration']) is not str or not doc['tracking_configuration']:
            fail('BUYER_TRACKING_CONFIGURATION_SCOPE')
        measurement = consume_configuration(doc['tracking_configuration'], scope, doc['tracking'], raw['goal'])
        tracking['measurement'] = measurement
        if measurement['goal_match'] != 'DECLARED_GOAL_EVENT_ALIGNED':
            blockers.append(measurement['goal_match'])
    test = strategy['test']
    maximum = limits['maximum_test_spend']
    budget = test['budget_minor']
    if not maximum or not budget or budget > maximum:
        blockers.append('DECLARE_A_TEST_BUDGET_WITHIN_WORKSPACE_CAP')
    if test['minimum_result_budget_feasible'] is not True or strategy['missing_inputs']:
        blockers.append('REVIEW_TEST_COST_AND_SAMPLE')
    binding = dict(workspace_sha256=scope['workspace_identity'], account_id=account, page_id=page,
                   currency=currency, destination_url=destination, goal=raw['goal'],
                   dataset_id=doc['tracking']['dataset_id'], budget_minor=budget,
                   protected_daily_minor=limits['protected_daily_spend'],
                   creatives_sha256=digest(frozen), profile_sha256=digest(scope), intake_sha256=digest(doc))
    intake = dict(binding=binding, selections=selection, frozen=frozen, blockers=blockers,
                  business=business, tracking=tracking, strategy=strategy, partner_briefs=briefs,
                  competitor_observation=competitor, creative_mode=creative_mode,
                  catalog_review=catalog, policy_review=policy)
    if 'selected_strategy' in doc:
        from .strategy_plan import compile_selection, managed_nodes
        compiled = compile_selection(selected_strategy, account=account, currency=currency,
                                     goal=raw['goal'], dataset=doc['tracking']['dataset_id'],
                                     asset_ids=[item[2] for item in selection], maximum_minor=maximum)
        strategy['strategy_selected'] = compiled
        intake['compiled_strategy'] = compiled
        nodes = managed_nodes(compiled, test_limit=budget, daily_limit=limits['protected_daily_spend'])
        if nodes is None:
            blockers.append('COMPILED_OBJECT_GRAPH_REQUIRES_SEPARATE_EXECUTION_PATH')
        else:
            binding['managed_strategy'] = dict(nodes=nodes, plan_sha256=compiled['plan_sha256'],
                                               budget_owner=compiled['budget_owner'],
                                               budget_schedule=compiled['budget_schedule'],
                                               mode=compiled['mode'], sequence=compiled['sequence'],
                                               source_campaign_id=compiled['source_campaign_id'])
    if 'existing_structure' in doc:
        if (type(doc['existing_structure']) is not str or not doc['existing_structure']
                or intake.get('compiled_strategy', {}).get('mode') != 'APPEND'):
            fail('BUYER_EXISTING_STRUCTURE_SCOPE')
        prior = progress(workspace.directory, Path(doc['existing_structure']))
        if (prior['status'] != 'FIRST_LAUNCH_STRUCTURE_RECORDED'
                or (prior['account_id'], prior['page_id'], prior['workspace_identity_sha256']) !=
                (account, page, scope['workspace_identity'])):
            fail('BUYER_EXISTING_STRUCTURE_SCOPE')
        parents = {row['id']: row for row in prior['structure']}
        for node in intake['compiled_strategy']['nodes']:
            identifier = node.get('remote_id')
            if identifier is not None and (identifier not in parents
                    or parents[identifier]['operation'] != 'CREATE_' + node['kind']):
                fail('BUYER_EXISTING_STRUCTURE_SCOPE')
        binding['existing_structure_sha256'] = digest(prior['structure'])
        intake['previous_structure_plan_sha256'] = prior['plan_sha256']
    return intake


def _launch(workspace, state, directory):
    launch = progress(workspace, directory)
    binding = state['intake']['binding']
    if (launch['plan_sha256'] != state['plan_sha256']
            or launch['account_id'] != binding['account_id']
            or launch['workspace_identity_sha256'] != binding['workspace_sha256']):
        fail('BUYER_ORIGINAL_PLAN_MISMATCH')
    return launch


def _period_review(workspace, directory, launch, source):
    from .first_launch_delivery import delivery_review
    from .operator_review import performance_diagnose

    structured(source, 'media-buyer-review-v1', ('ad_id', 'windows'),
               ('evidence', 'stage_evidence', 'shopify_review', 'shopify_snapshot', 'spend', 'native_spend_snapshot',
                'minor_digits', 'commerce_csv', 'funnel', 'historical_costs', 'focused_readbacks', 'store_funnel_export'))
    ad = pattern(source['ad_id'], r'[1-9][0-9]{0,39}')
    windows = source['windows']
    if not isinstance(windows, list) or not 1 <= len(windows) <= 2:
        fail('BUYER_REVIEW_WINDOW_COUNT')
    if len(windows) > 1 and 'evidence' in source:
        fail('BUYER_ATTACH_EVIDENCE_TO_EACH_WINDOW')
    batch = 'creative_research_batch' in launch
    if not batch and launch.get('ad_id') != ad:
        fail('BUYER_REVIEW_ORIGINAL_AD_REQUIRED')
    reports = []
    for window in windows:
        keys = ('receipt', 'since', 'until')
        object_only(window, (*keys, 'evidence'), keys)
        if 'evidence' in window and 'evidence' in source:
            fail('BUYER_ATTACH_EVIDENCE_TO_EACH_WINDOW')
        evidence = window.get('evidence', source.get('evidence'))
        reports.append(delivery_review(workspace, directory, Path(window['receipt']), window['since'],
                                       window['until'], ad_id=ad if batch else None,
                                       evidence_path=Path(evidence) if evidence is not None else None))
    selected = progress(workspace, directory, ad_id=ad)
    if selected['plan_sha256'] != launch['plan_sha256']:
        fail('BUYER_ORIGINAL_PLAN_MISMATCH')
    context = selected['activation_context']
    event = launch['declared_result_type']
    diagnosis = None

    def period(report):
        if any(report.get(key) not in (None, context[key]) for key in ('currency', 'timezone')):
            fail('BUYER_REPORT_ACCOUNT_CONTEXT_MISMATCH')
        metrics = report['metrics'] or {}

        def number(key, currency=False):
            value = metrics.get(key, {}).get('value')
            if value is None:
                return None
            amount = Decimal(value)
            if currency:
                return _minor_money(amount, context['currency'])
            return int(amount) if amount == amount.to_integral_value() else None

        value = dict(account_id=report['account_id'], currency=context['currency'], timezone=context['timezone'],
                     scope_id=ad, attribution=report.get('attribution') or 'UNAVAILABLE',
                     coverage='COMPLETE' if report.get('recommendation') == 'REVIEW_SAME_AD_AND_WINDOW'
                     else 'PARTIAL', delivery='UNKNOWN', **report['reviewed_window'],
                     impressions=number('impressions'), spend_minor=number('spend', currency=True),
                     clicks=number('clicks'), landing_views=number('landing_page_views'),
                     conversions=number({'PURCHASE': 'website_purchases', 'LEAD': 'website_leads'}.get(event)),
                     conversion_value_minor=number('website_purchase_value', currency=True) if event == 'PURCHASE' else None)
        # The collector fixes impression report time. A requested event/profile stays separate from actual coverage.
        if report.get('attribution'):
            value.update(report_time='REQUESTED_IMPRESSION', result_event=event or 'UNSPECIFIED')
        return value

    periods = [period(report) for report in reports]
    evidence = reports[-1]['launch_evidence']
    if evidence.get('creative') and evidence['creative'].get('state') == 'RETURNED':
        status = evidence['creative'].get('observed_ad_status', {}).get('effective_status')
        if status in ('DISAPPROVED', 'WITH_ISSUES'):
            periods[-1]['delivery'] = 'WITH_ISSUES'
        elif status in ('PAUSED', 'CAMPAIGN_PAUSED', 'ADSET_PAUSED'):
            periods[-1]['delivery'] = 'PAUSED'
    if len(reports) == 2:
        diagnosis = performance_diagnose(dict(schema='operator-diagnosis-v1', before=periods[0], after=periods[1],
                                                minimum_results=launch['minimum_results'],
                                                stage_evidence=source.get('stage_evidence', {})))
        if evidence.get('creative') and evidence['creative'].get('state') == 'RETURNED':
            for stage in diagnosis['stages']:
                if stage['stage'] == 'creative' and stage['state'] in ('EVIDENCE_MISSING', 'REFERENCE_UNVERIFIED'):
                    stage.update(state='ORIGINAL_CREATIVE_PRESENT', evidence_basis=evidence['state'],
                                 evidence_sha256=evidence['evidence_sha256'])
                elif stage['stage'] == 'delivery' and periods[-1]['delivery'] in ('PAUSED','WITH_ISSUES'):
                    stage.update(state='OBSERVED_ISSUE', evidence_basis=evidence['state'],
                                 evidence_sha256=evidence['evidence_sha256'])
    funnel = _funnel(source.get('funnel'), periods[-1])
    shopify = None
    csv = None
    if 'commerce_csv' in source:
        from .commerce_csv import reconcile

        if 'shopify_review' in source or 'shopify_snapshot' in source:
            fail('BUYER_COMMERCE_SOURCE_AMBIGUOUS')
        data = source['commerce_csv']
        keys = ('orders', 'refunds', 'spend', 'store_id', 'timezone')
        object_only(data, (*keys, 'refund_basis'), keys)
        if data['timezone'] != context['timezone']:
            fail('BUYER_COMMERCE_TIMEZONE_MISMATCH')
        window = periods[-1]
        csv = reconcile(Path(data['orders']), Path(data['refunds']), Path(data['spend']),
                        data['store_id'], launch['account_id'], context['currency'],
                        window['since'], window['until'], data.get('refund_basis', 'event'))
    if 'spend' in source and 'native_spend_snapshot' in source:
        fail('BUYER_COMMERCE_SPEND_SOURCE_AMBIGUOUS')
    if 'shopify_review' in source and 'shopify_snapshot' in source:
        fail('BUYER_COMMERCE_SOURCES_CONFLICT')
    if 'shopify_review' in source or 'shopify_snapshot' in source:
        from .shopify_graphql import reconcile_spend, review

        if 'shopify_snapshot' in source:
            from .shopify_transport import read_snapshot
            shopify = read_snapshot(workspace, source['shopify_snapshot'])
            doc = shopify
        else:
            doc = load_input(source['shopify_review'])
            shopify = review(doc)
        current = periods[-1]
        if (doc['since'], doc['until'], doc['currency']) != (current['since'], current['until'], current['currency']):
            fail('BUYER_SHOPIFY_WINDOW_OR_CURRENCY_MISMATCH')
        if 'native_spend_snapshot' in source:
            digits = {'USD': 2, 'EUR': 2, 'JPY': 0}.get(context['currency'])
            if digits is None or source.get('minor_digits', digits) != digits:
                fail('SHOPIFY_SPEND_MINOR_DIGITS_INVALID')
            shopify = reconcile_spend(doc, shopify, Path(source['native_spend_snapshot']), launch['account_id'],
                                      context['timezone'], digits, native_workspace=workspace,
                                      cost_path=source.get('historical_costs'))
        elif 'spend' in source:
            shopify = reconcile_spend(doc, shopify, Path(source['spend']), launch['account_id'],
                                      context['timezone'], source.get('minor_digits'),
                                      cost_path=source.get('historical_costs'))
        elif 'historical_costs' in source:
            fail('BUYER_COMMERCE_COST_REQUIRES_RECONCILIATION')
    elif any(key in source for key in ('spend', 'native_spend_snapshot', 'minor_digits', 'historical_costs')):
        fail('BUYER_SHOPIFY_REVIEW_REQUIRED')
    if 'store_funnel_export' in source:
        from .commerce_report import attach_funnel_export

        reconciled = shopify or csv
        if reconciled is None or reconciled['status'] != 'LOCAL_COMMERCE_RECONCILED':
            fail('COMMERCE_FUNNEL_EXPORT_RECONCILIATION_REQUIRED')
        attached = attach_funnel_export(reconciled,source['store_funnel_export'],context['timezone'])
        if shopify is not None:
            shopify = attached
        else:
            csv = attached
    queue = _diagnostic_queue(workspace, reports[-1], directory.parent)
    if 'focused_readbacks' in source:
        _consume_focus(queue, source['focused_readbacks'], launch['workspace_identity_sha256'], periods[-1])
    return dict(ad_id=ad, reports=reports, periods=periods, diagnosis=diagnosis, shopify=shopify,
                commerce_csv=csv, funnel=funnel, diagnostic_queue=queue,
                real_platform_authorization_verified_here=False)


def _diagnostic_queue(workspace, report, case_directory):
    focus = report.get('account_spend_focus', {})
    window = report['reviewed_window']
    account = report['account_id']
    prioritized = focus.get('ad_ids', []) if focus.get('state') == 'OBSERVED_SPEND_FOCUS' else []
    targets = list(dict.fromkeys((report['ad_id'], *prioritized)))
    output = []
    for index, identifier in enumerate(targets[:6], 1):
        pattern(identifier, r'[1-9][0-9]{0,39}')
        target = case_directory / 'diagnostic' / f"{window['since']}-{window['until']}-{identifier}"
        command = ['native-insights', '--workspace', str(workspace), '--account-id', account,
                   '--level', 'ad', '--id', identifier, '--since', window['since'], '--until', window['until'],
                   '--include-object-context', '--metric', 'spend', '--metric', 'impressions',
                   '--metric', 'actions', '--metric', 'action_values', '--output', str(target)]
        output.append(dict(ad_id=identifier, position=index, observed_spend_focus=identifier in prioritized,
                           argv=command, operation_state='PREVIEW_REQUIRES_SCOPED_READ_CONFIRMATION'))
    return dict(state='QUEUED', targets=output, omitted=max(len(targets)-len(output), 0),
                spend_focus_state=focus.get('state', 'UNAVAILABLE'), actions_authorized=False)


def _consume_focus(queue, files, workspace_hash, prior_period):
    from .local_paths import checked_path
    from .native_insights import NativeInsightsPlan

    if type(files) is not list or not 1 <= len(files) <= len(queue['targets']):
        fail('BUYER_FOCUSED_READBACK_COUNT')
    targets = {item['ad_id']: item for item in queue['targets']}
    for file in files:
        if type(file) is not str or not file:
            fail('BUYER_FOCUSED_READBACK_SCOPE')
        path = checked_path(file, link_error='BUYER_FOCUSED_READBACK_SCOPE', volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')

        def validate(payload):
            keys = ('schema', 'workspace_sha256', 'account_id', 'source_context', 'report', 'object_context')
            object_only(payload, keys, keys)
            source, details = payload['source_context'], payload['object_context']
            query = source['query']
            fields = tuple(NativeInsightsPlan.__dataclass_fields__)
            object_only(query, fields, fields)
            plan = NativeInsightsPlan(**dict(query, breakdowns=tuple(query['breakdowns']), fields=tuple(query['fields'])))
            account = prior_period['account_id']
            expected = ('native-insights-snapshot-v1', workspace_hash, account, 'ad', account,
                        prior_period['since'], prior_period['until'], prior_period['currency'], prior_period['timezone'])
            actual = (payload['schema'], payload['workspace_sha256'], payload['account_id'], plan.level, plan.account_id,
                      plan.since, plan.until, source.get('currency'), source.get('timezone'))
            if (actual != expected or plan.breakdowns or plan.daily or plan.action_report_time != 'impression'
                    or (details.get('object_id'),details.get('account_id')) != (plan.object_id,account)
                    or plan.object_id not in targets or 'readback' in targets[plan.object_id]):
                fail('BUYER_FOCUSED_READBACK_SCOPE')

        payload, fingerprint = Journal.read_snapshot(path, validate)
        details = payload['object_context']
        item = targets[details['object_id']]
        report = payload['report']
        event = details.get('adset', {}).get('event') or {}
        unknown_semantics = event.get('comparison_requires_event_semantics', True)
        disposition = 'VERIFY_CONVERSION_DEFINITION' if unknown_semantics else 'CHECK_SAME_SIGNAL_AND_WINDOW'
        if report.get('coverage') != 'COMPLETE':
            disposition = 'RESUME_OR_RECONCILE_READ_COVERAGE'
        elif details.get('effective_status') in ('DISAPPROVED','WITH_ISSUES'):
            disposition = 'REVIEW_POLICY_AND_ORIGINAL_CREATIVE'
        item.update(operation_state='SCOPED_SOURCE_ATTACHED',
                    readback=dict(snapshot_sha256=fingerprint, source=str(path), coverage=report.get('coverage'),
                                  details=details, row_count=len(report.get('rows', [])),
                                  action_report_time='REQUESTED_IMPRESSION', attribution_comparison='NOT_INFERRED',
                                  next_review=disposition))
    queue['state'] = 'EVIDENCE_ATTACHED'


def _funnel(source, period):
    from .operator_review import ratio

    if source is None:
        return dict(state='FUNNEL_EVIDENCE_MISSING')
    identity = ('account_id', 'scope_id', 'since', 'until', 'currency', 'timezone', 'attribution')
    structured(source, 'media-buyer-funnel-v1', (*identity, 'evidence_ref', 'coverage', 'counts'))
    if any(source[key] != period[key] for key in identity):
        fail('BUYER_FUNNEL_SCOPE_MISMATCH')
    if source['coverage'] not in ('COMPLETE', 'PARTIAL'):
        fail('BUYER_FUNNEL_COVERAGE_INVALID')
    pattern(source['evidence_ref'], r'EV_[A-Z0-9_]{1,80}')
    keys = ('landing_page_views', 'add_to_cart', 'initiated_checkout', 'purchase')
    object_only(source['counts'], keys, keys)
    counts = {key: minor(source['counts'][key]) for key in keys}
    ratios = {}
    if source['coverage'] == 'COMPLETE':
        ratios = {new + '_over_' + old: ratio(counts[new], counts[old])
                  for old, new in zip(keys, keys[1:])}
    return dict(state='LOCAL_FUNNEL_EVIDENCE', coverage=source['coverage'], counts=counts, ratios=ratios,
                evidence_ref=source['evidence_ref'], source='USER_SUPPLIED_EXPORT',
                not_used_as_independent_platform_evidence=True)


def _proposal(workspace, directory, state, source):
    from .first_launch_update import ACTIONS, CHANGE_FIELDS, run

    structured(source, 'media-buyer-proposal-v1',
               ('action', 'evidence', 'counterevidence', 'stop_condition', 'ad_id', 'review_sha256'),
               ('budget_minor', 'change'))
    if 'change' in source and source['action'] not in CHANGE_FIELDS:
        fail('BUYER_CHANGE_NOT_APPLICABLE')
    review = state['review']
    if (not review or source['review_sha256'] != digest(review) or review['ad_id'] != source['ad_id']):
        fail('BUYER_OPTIMIZATION_REVIEW_MISMATCH')
    from .operator_review import evidence_text

    justifications = {key: evidence_text(source[key]) for key in ('evidence', 'counterevidence', 'stop_condition')}
    action = source['action']
    budget = minor(source.get('budget_minor'))
    diagnosis = review['diagnosis']
    focused = next((item.get('readback') for item in review.get('diagnostic_queue', {}).get('targets', [])
                    if item['ad_id'] == review['ad_id']), None)
    if action in ('SET_ADSET_DAILY_BUDGET','SET_ADSET_BID_AMOUNT') and focused is not None and (
            focused['coverage'] != 'COMPLETE' or focused['next_review'] != 'CHECK_SAME_SIGNAL_AND_WINDOW'):
        fail('BUYER_FOCUSED_SIGNAL_REQUIRES_REVIEW')
    if action == 'SET_ADSET_DAILY_BUDGET':
        binding = state['intake']['binding']
        caps = [value for value in (binding['budget_minor'], binding['protected_daily_minor']) if value is not None]
        if not diagnosis or not diagnosis['comparable'] or diagnosis['followup'] or budget is None or budget > min(caps):
            fail('BUYER_BUDGET_REVIEW_EVIDENCE_INSUFFICIENT')
    elif budget is not None:
        fail('BUYER_BUDGET_NOT_APPLICABLE')
    if action == 'SET_ADSET_BID_AMOUNT' and (not diagnosis or not diagnosis['comparable'] or diagnosis['followup']):
        fail('BUYER_BID_REVIEW_EVIDENCE_INSUFFICIENT')
    if action in ACTIONS:
        receipt = run(workspace, directory, action, budget, None, 'plan',
                      ad_id=review['ad_id'], change=source.get('change'))
    elif action in ('REVIEW_BID', 'REVIEW_AUDIENCE', 'REVIEW_CREATIVE', 'REVIEW_SCHEDULE'):
        receipt = dict(status='MANUAL_CHANGE_REVIEW_REQUIRED', action=action, write_supported=False,
                       next_steps=['FREEZE_BEFORE_AND_AFTER', 'REVIEW_AFFECTED_ADS_AND_BUDGET',
                                   'REQUEST_SEPARATE_APPROVAL_IN_ADS_MANAGER', 'READ_BACK_SAME_OBJECT'])
    else:
        fail('BUYER_UNSUPPORTED_OPTIMIZATION')
    return dict(receipt=receipt, review_sha256=source['review_sha256'], ad_id=review['ad_id'],
                **justifications, actions_authorized=False)


def _record_delivery_learning(workspace, directory, state, launch, case):
    from .creative_research import _frozen, _record

    review = state['review']
    if not review:
        fail('BUYER_LEARNING_NEEDS_REVIEW')
    report = review['reports'][-1]
    evidence = report['launch_evidence']
    creative = evidence.get('creative') or {}
    if (report['delivery_state'] != 'POSITIVE_IMPRESSIONS_OBSERVED'
            or report['recommendation'] != 'REVIEW_SAME_AD_AND_WINDOW'
            or evidence.get('state') != 'SUPPLIED_MATCHING_SCOPE' or creative.get('state') != 'RETURNED'):
        fail('BUYER_LEARNING_NEEDS_SAME_WINDOW_AND_CREATIVE')
    selected = progress(workspace, directory, ad_id=review['ad_id'])
    if (selected['plan_sha256'] != launch['plan_sha256']
            or selected['stages'][-1]['readback']['object']['creative_id'] != creative.get('id')):
        fail('BUYER_LEARNING_ORIGINAL_CREATIVE_REQUIRED')
    if 'creative_research_batch' in selected:
        asset = next((row['material_sha256'] for row in selected['batch_items']
                      if row['operation'] == 'CREATE_AD' and row['object_id'] == review['ad_id']), None)
        selection = next((row for row in selected['creative_research_batch']
                          if row['asset_sha256'] == asset), None)
    else:
        selection = selected.get('creative_research')
    if not selection:
        fail('BUYER_LEARNING_NO_LINKED_CONCEPT')
    concept_id, variant_id, asset = (selection[key] for key in ('concept_id','variant_id','asset_sha256'))
    ws = journal_for(workspace, existing_only=True)
    with ws.locked(existing_only=True):
        profile = load(ws)
        account = state['intake']['binding']['account_id']
        current = _record(ws, profile, account)
        if _frozen(current, concept_id, variant_id, asset, allow_used=True) != selection:
            fail('BUYER_LEARNING_ORIGINAL_SELECTION_CHANGED')
        evidence_id = 'DELIVERY-' + digest([launch['plan_sha256'],review['ad_id'],report])[:40]
        window = report['reviewed_window']
        fact = ('原广告 ' + review['ad_id'] + ' 在 ' + window['since'] + ' 至 ' + window['until']
                + ' 的复盘记录有展示。素材版本与所附创意回读一致。')
        concept = dict(current['concepts'][concept_id], state='USED')
        update = dict(brand=None, concepts=[concept], reviews=[], sources=[
            dict(id=evidence_id, kind='OWN_PERFORMANCE', url=None, observed_at=evidence['captured_at'],
                 product=state['intake']['business']['fields']['PRODUCT']['value'], fact=fact)], learning=[
            dict(id=evidence_id,source_id=evidence_id,scope=case,
                 finding='保留该方向为已使用。展示仅用于确认曾用素材，继续用可比区间复核效果。',state='WATCH')])
        updated = _record(ws, profile, account, update)
    return dict(status='BUYER_LEARNING_RECORDED', case=case, ad_id=review['ad_id'], concept_id=concept_id,
                asset_sha256=asset, review_sha256=digest(review), evidence_ref=evidence_id,
                retained_candidates=[key for key,row in updated['concepts'].items() if row['state']=='AVAILABLE'],
                network_used=False, actions_authorized=False)


def run_case(workspace, case, phase, input_path, output=None):
    if (phase == 'report') != (output is not None):
        fail('BUYER_REPORT_OUTPUT_REQUIRED')
    workspace = Path(workspace).absolute()
    case = pattern(case, r'[a-z0-9][a-z0-9_-]{0,62}')
    needs_input = phase in ('prepare', 'review', 'optimize')
    if (input_path is not None) != needs_input:
        fail('BUYER_PHASE_INPUT_MISMATCH')
    ws = journal_for(workspace, existing_only=True)
    with ws.locked(existing_only=True):
        identity = workspace_identity(load(ws))
    source = load_input(input_path) if needs_input else None
    intake = _intake(source, ws) if phase == 'prepare' else None
    ledger = Journal(ws.directory / 'media-buyer' / case, stage_snapshots=True, existing_only=phase != 'prepare')
    path = ledger.directory / 'first-launch'
    scope = dict(schema='media-buyer-case-v1', case=case, workspace_sha256=identity)

    def validate(value):
        keys = (*scope, 'intake', 'plan_sha256', 'review', 'proposal')
        object_only(value, keys, keys)
        if any(value[k] != v for k, v in scope.items()):
            fail('BUYER_CASE_IDENTITY_MISMATCH')
        if value['intake']['binding']['workspace_sha256'] != identity:
            fail('BUYER_CASE_INTAKE_WORKSPACE_CHANGED')
        if value['plan_sha256'] is not None:
            pattern(value['plan_sha256'], r'[a-f0-9]{64}')

    launch = None
    with ledger.locked(existing_only=phase != 'prepare'):
        previous, damaged = ledger.load(validate)
        if damaged or ledger.unpublished_snapshot_count():
            fail('BUYER_CASE_RECOVERY_REVIEW_REQUIRED')
        if phase == 'prepare':
            if previous and (path.exists() or path.is_symlink()) and digest(previous['intake']) != digest(intake):
                fail('BUYER_CASE_FROZEN_AFTER_PLAN_USE_NEW_CASE')
            state = previous if previous and digest(previous['intake']) == digest(intake) else dict(
                scope, intake=intake, plan_sha256=None, review=None, proposal=None)
        elif previous is None:
            fail('BUYER_PREPARE_CASE_FIRST')
        else:
            state = previous.copy()
        intake = state['intake']
        binding = intake['binding']
        if phase == 'plan' and not intake['blockers'] and state['plan_sha256'] is None:
            if not (path.exists() or path.is_symlink()):
                prepare(workspace, path, account_id=binding['account_id'], page_id=binding['page_id'],
                        creative_selection=intake['selections'], operator_intake=binding)
            with ws.locked(existing_only=True):
                bound, sha = current_plan(load(ws), path)
            if bound.get('operator_intake') != binding:
                fail('BUYER_ORIGINAL_PLAN_MISMATCH')
            state['plan_sha256'] = sha
        if state['plan_sha256'] is not None:
            launch = _launch(workspace, state, path)
        if phase in ('review', 'optimize', 'learn') and launch is None:
            fail('BUYER_PLAN_REQUIRED')
        if phase == 'review':
            result = _period_review(workspace, path, dict(launch, minimum_results=intake['strategy']['test']['minimum_results']), source)
            if state['review'] != result:
                state.update(review=result, proposal=None)
        elif phase == 'optimize':
            state['proposal'] = _proposal(workspace, path, state, source)
        elif phase == 'learn':
            return _record_delivery_learning(workspace,path,state,launch,case)
        if state != previous:
            ledger.save(state, validate)

    if phase == 'report':
        from .commerce_report import export

        review = state['review']
        result = (review.get('shopify') or review.get('commerce_csv')) if review else None
        if not result or result.get('status') != 'LOCAL_COMMERCE_RECONCILED':
            fail('COMMERCE_REPORT_RECONCILIATION_REQUIRED')
        return export(result, output, review=dict(ad_id=review['ad_id'], case=case, sha256=digest(review),
                                                 next_review=review['reports'][-1].get('next_review')))
    next_step = 'ADD_EVIDENCE' if intake['blockers'] else 'FREEZE_PLAN'
    routes = {}
    if launch:
        next_step = launch.get('next_action') or launch['status']
        args = ['--workspace', str(workspace), '--plan-dir', str(path)]
        routes = {name: ['workspace-first-launch-' + command, *args]
                  for name, command in (('check_drafts', 'check'), ('continue', 'progress'), ('media', 'media'),
                                        ('preview', 'preview'), ('activation', 'activation'), ('handoff', 'handoff'),
                                        ('update', 'update'), ('evidence', 'evidence'))}
    return dict(status='MEDIA_BUYER_CASE', case=case, intake=intake, plan_dir=str(path) if launch else None,
                launch=launch, review=state['review'], review_sha256=digest(state['review']) if state['review'] else None,
                proposal=state['proposal'], next_step=next_step, customer_commands=routes,
                network_used=False, actions_authorized=False)
