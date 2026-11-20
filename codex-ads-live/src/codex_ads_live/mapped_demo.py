"""Fixed synthetic mapped-action demonstration; no live input or transport."""
from datetime import datetime, timedelta, timezone
from .activation import prepare_impact
from .journal import Journal, digest
from .local_paths import checked_path
from .mapped_actions import MappedActionLedger
from .mapped_service import SyntheticMappedService
from .meta_write_mapping import blank_draft, load_draft, validate_draft, _write_new
from .monitor_tools import new_directory
from .write_snapshots import SnapshotPlan, collect_snapshots, normalize_snapshot
from .materials import check_media_files, synthetic_material, export_material
from .material_bundle import load_material_packet
from .validation import aware_time, fail, strict_json

TARGETING = dict(geo_locations=dict(countries=['US']), age_min=18, age_max=65)
ALLOW=dict(account_id='100',CAMPAIGN=['200'],ADSET=['300'],AD=['400'],CREATIVE=['500'],PAGE=['600'])
FIRST_LEVELS=('CAMPAIGN', 'ADSET', 'CREATIVE', 'AD')
FIRST_ACTIONS={level: 'SIM_ACTION_FIRST_' + level for level in FIRST_LEVELS}
FIXTURES={
    'ACCOUNT':dict(id='act_100',account_id='100',currency='USD',timezone_name='UTC',account_status=1),
    'CAMPAIGN':dict(id='200',account_id='100',status='ACTIVE',effective_status='ACTIVE',objective='OUTCOME_TRAFFIC',
        buying_type='AUCTION',special_ad_categories=[],daily_budget='0',lifetime_budget='0'),
    'ADSET':dict(id='300',account_id='100',campaign_id='200',status='ACTIVE',effective_status='CAMPAIGN_PAUSED',
        daily_budget='1000',lifetime_budget='0',optimization_goal='LINK_CLICKS',billing_event='IMPRESSIONS',
        bid_strategy='LOWEST_COST_WITHOUT_CAP',destination_type='WEBSITE'),
    'AD':dict(id='400',account_id='100',campaign_id='200',adset_id='300',status='PAUSED',effective_status='PAUSED',creative={'id':'500'}),
    'CREATIVE':dict(id='500',account_id='100',actor_id='600'),'PAGE':dict(id='600')}


def example_draft(operation):
    value=blank_draft(operation);value['account_id']='100'
    value['parameters']={
        'CREATE_CAMPAIGN':dict(name='Synthetic campaign',objective='OUTCOME_TRAFFIC',buying_type='AUCTION',special_ad_categories=[],is_adset_budget_sharing_enabled=False,status='PAUSED'),
        'CREATE_ADSET':dict(name='Synthetic set',campaign_id='200',daily_budget=1200,billing_event='IMPRESSIONS',optimization_goal='LINK_CLICKS',bid_strategy='LOWEST_COST_WITHOUT_CAP',destination_type='WEBSITE',targeting=dict(geo_locations=dict(countries=['US']),age_min=18,age_max=65),status='PAUSED'),
        'CREATE_AD':dict(name='Synthetic ad',adset_id='300',creative={'creative_id':'500'},status='PAUSED'),
        'SET_ADSET_DAILY_BUDGET':dict(daily_budget=1200),'SET_STATUS':dict(status='ACTIVE')}[operation]
    if operation=='SET_ADSET_DAILY_BUDGET': value['target_id']='300'
    if operation=='SET_STATUS': value.update(target_id='400',target_level='AD')
    if operation=='CREATE_AD': value['context'].update(page_id='600',material_sha256='a'*64)
    return value


def run_mapped_demo(output, *, keep_interrupted=False):
    root=new_directory(output);results={};reads=0;sends=0;NOW=datetime.now(timezone.utc)
    scenarios=[(x,x) for x in ('CREATE_CAMPAIGN','CREATE_ADSET','CREATE_AD','SET_ADSET_DAILY_BUDGET','SET_STATUS')]
    scenarios += [('CHANGED','SET_ADSET_DAILY_BUDGET'),('REVOKED','CREATE_AD'),('CRASH_AFTER_SEND','CREATE_AD'),('CRASH_BEFORE_SEND','CREATE_CAMPAIGN')]
    for scenario,op in scenarios:
        folder=root/scenario;service=SyntheticMappedService(folder/'service');service.seed(FIXTURES, budget_targeting={'300': TARGETING})
        media_root=folder/'assets';material=None;value=example_draft(op)
        coverage=None;material_packet=None
        if op in ('CREATE_AD','SET_STATUS','SET_ADSET_DAILY_BUDGET'):
            material,payload=synthetic_material(NOW);media_root.mkdir();(media_root/material['media'][0]['path']).write_bytes(payload)
            service.seed_material(material)
            if op != 'CREATE_AD':
                value,coverage=prepare_impact(value,ALLOW,service,{material['creative_id']:material},media_root,
                                              budget=op=='SET_ADSET_DAILY_BUDGET',clock=lambda:NOW)
                export_material(material,media_root,folder/'material-review',clock=lambda:NOW)
            else:
                value['context']['material_sha256']=None
                material_packet=folder/'material-review'
                export_material(material,media_root,material_packet,value,clock=lambda:NOW)
                value=load_draft(material_packet/'draft.json')
        ledger=MappedActionLedger(folder/'ledger',service,simulation=True,material_root=media_root,
                                  material_packet_directory=material_packet,clock=lambda:NOW,monotonic=lambda:0)
        baseline=collect_snapshots(value,ALLOW,service.snapshot_factory,clock=lambda:NOW,monotonic=lambda:0)
        review=ledger.submit('SIM_ACTION_DEMO',value,baseline,material=material if op=='CREATE_AD' else None,coverage=coverage)
        ledger.approve_simulated('SIM_ACTION_DEMO',review['review_sha256'],(NOW+timedelta(minutes=2)).isoformat())
        _write_new(folder,'REVIEW.json',review)
        if scenario=='CHANGED': service.simulate_change('ADSET','300',{'daily_budget':'1500'})
        if scenario=='REVOKED': ledger.revoke('SIM_ACTION_DEMO');result=ledger.status()['actions']['SIM_ACTION_DEMO']
        else:
            failure={'CRASH_AFTER_SEND':'AFTER_SEND','CRASH_BEFORE_SEND':'BEFORE_SEND'}.get(scenario)
            result=ledger.execute_simulated('SIM_ACTION_DEMO',failure=failure)
            if failure:
                _write_new(folder,'INTERRUPTED.json',result)
                if not keep_interrupted:
                    # Re-open both journals; do not send again, even with no receipt.
                    restarted=MappedActionLedger(folder/'ledger',SyntheticMappedService(folder/'service'),simulation=True,clock=lambda:NOW)
                    result=restarted.reconcile('SIM_ACTION_DEMO')
        _write_new(folder,'RESULT.json',result);results[scenario]=result
        reads+=sum(len(t.calls) for t in service.transports);sends+=service.send_attempts
    result=dict(schema='mapped-synthetic-demo-v1',mode='MOCK',scenarios=results,synthetic_object_gets=reads,
        synthetic_send_attempts=sends,real_meta_requests=0,human_identity_verified=False,real_write_authorized=False,
        limitation='FIXED_SYNTHETIC_FIXTURES_NOT_META_CAS_IDEMPOTENCY_OR_CUSTOMER_ACCEPTANCE')
    _write_new(root,'RESULT.json',result)
    # Journal snapshots and durable receipts remain independently inspectable.
    import hashlib
    manifest=[dict(path=str(p.relative_to(root)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(root.rglob('*')) if p.is_file()]
    _write_new(root,'RECEIPT.json',dict(mode='MOCK',files=manifest,real_meta_requests=0))
    return result


def _first_launch_root(path):
    return checked_path(path, link_error='FIRST_LAUNCH_PATH_LINK', volume_error='FIRST_LAUNCH_VOLUME_NOT_MOUNTED')


def _first_launch_history(ledger):
    status = ledger.recorded_exit_actions()
    if status['stopped']:
        fail('FIRST_LAUNCH_LEDGER_STOPPED')
    actions = {item['action_id']: item['state'] for item in status['actions']}
    prefix = [FIRST_ACTIONS[level] for level in FIRST_LEVELS[:len(actions)]]
    if not prefix or set(actions) != set(prefix) or any(actions[key] != 'VERIFIED_SIM' for key in prefix[:-1]):
        fail('FIRST_LAUNCH_LEDGER_SEQUENCE')
    return actions


def _record_first_receipt(root, level, receipt):
    path = _first_launch_root(root/(level + '_RECEIPT.json'))
    if not path.exists():
        _write_new(root, path.name, receipt)
        return
    if not path.is_file() or path.stat().st_size > 64_000 or strict_json(path.read_text(encoding='utf-8')) != receipt:
        fail('FIRST_LAUNCH_SAVED_RECEIPT_MISMATCH')


def run_first_launch_demo(output, *, resume=False):
    """Continue under the same owner lock without resending a recorded action."""
    root = _first_launch_root(output) if resume else new_directory(output)
    asset_root = _first_launch_root(root/'assets')
    material_dir = _first_launch_root(root/'material-review')
    service = SyntheticMappedService(root/'service', existing_only=resume)
    ledger = MappedActionLedger(root/'ledger', service, simulation=True, material_root=asset_root,
                                material_packet_directory=material_dir, existing_only=resume)
    if resume:
        _first_launch_history(ledger)
    owner = Journal(root/'first-launch-owner')
    with owner.locked():
        return _run_first_launch(root, service, ledger, asset_root, material_dir, resume=resume)


def _run_first_launch(root, service, ledger, asset_root, material_dir, *, resume):
    history = _first_launch_history(ledger) if resume else {}
    now = datetime.now(timezone.utc)
    clock = lambda: now
    started = aware_time(ledger.inspect(FIRST_ACTIONS['CAMPAIGN'])['packet']['bundle']['started_at']) if resume else now
    if started > now:
        fail('FIRST_LAUNCH_CLOCK_ROLLBACK')
    # The first run uses a fixed fixture clock. A continuation checks rights and
    # approvals against its own current time, rather than reviving the old window.
    ledger.clock = clock
    material, payload = synthetic_material(started)
    if resume:
        check_media_files(material, asset_root)
    else:
        service.seed({level: FIXTURES[level] for level in ('ACCOUNT', 'PAGE')})
        asset_root.mkdir()
        (asset_root/material['media'][0]['path']).write_bytes(payload)
    allowed = {key: [] for key in FIRST_LEVELS} | {'account_id': ALLOW['account_id'], 'PAGE': ALLOW['PAGE'].copy()}
    drafts = {level: example_draft('CREATE_' + level) for level in ('CAMPAIGN', 'ADSET', 'AD')}
    link = material['creative']['object_story_spec']['link_data']
    creative = blank_draft('CREATE_CREATIVE')
    creative['account_id'] = allowed['account_id']
    creative['context'].update(page_id=material['page_id'], material_sha256=digest(material))
    creative['parameters'].update(name='Synthetic creative', image_hash=link['image_hash'],
                                   destination_url=link['link'], primary_text=link['message'],
                                   headline=link['name'], description=link['description'],
                                   call_to_action=link['call_to_action']['type'])
    drafts['CREATIVE'] = creative
    for draft in drafts.values():
        validate_draft(draft)

    steps = {}
    readback = None
    for level in FIRST_LEVELS:
        action = FIRST_ACTIONS[level]
        draft = drafts[level]
        if level == 'ADSET':
            draft['parameters']['campaign_id'] = steps['CAMPAIGN']['object_id']
        if level == 'AD':
            creative_id = steps['CREATIVE']['object_id']
            material.update(creative_id=creative_id, creative=service.material_content(creative_id))
            draft['parameters'].update(adset_id=steps['ADSET']['object_id'], creative={'creative_id': creative_id})
        if action in history:
            saved = ledger.inspect(action)['packet']['draft']
            if any(saved[k] != draft[k] for k in ('operation', 'account_id', 'target_id', 'target_level', 'parameters')):
                fail('FIRST_LAUNCH_DRAFT_SCOPE_CHANGED')
            if saved['context']['page_id'] != draft['context']['page_id']:
                fail('FIRST_LAUNCH_DRAFT_SCOPE_CHANGED')
            if level == 'CREATIVE' and saved['context']['material_sha256'] != draft['context']['material_sha256']:
                fail('FIRST_LAUNCH_DRAFT_SCOPE_CHANGED')
            if history[action] not in ('VERIFIED_SIM', 'UNKNOWN'):
                fail('FIRST_LAUNCH_RECORDED_ACTION_REQUIRES_REVIEW')
            outcome=ledger.reconcile(action)
            if outcome['readback_outcome'] != 'VERIFIED_SIM' and outcome['state']=='VERIFIED_SIM':
                fail('FIRST_LAUNCH_EXISTING_READBACK_MISMATCH')
        else:
            if resume and _first_launch_root(root/(level + '_RECEIPT.json')).exists():
                fail('FIRST_LAUNCH_UNEXPECTED_RECEIPT')
            if level == 'AD':
                draft = _prepare_first_ad(draft, material, asset_root, material_dir, clock=clock)
            scoped = collect_snapshots(draft, allowed, service.snapshot_factory, clock=clock,
                                       synthetic_creative=level == 'CREATIVE')
            review = ledger.submit(action, draft, scoped, material=material if level == 'AD' else None)
            ledger.approve_simulated(action, review['review_sha256'], (now+timedelta(minutes=2)).isoformat())
            outcome = ledger.execute_simulated(action)
        receipt = outcome['receipt']
        steps[level] = dict(action_id=action, state=outcome['state'],
                            error_code=outcome.get('readback_reason') or outcome['last_error'],
                            object_id=receipt['object_id'] if receipt else None,
                            configured_status=receipt['configured_status'] if receipt else None,
                            review_sha256=outcome['review_sha256'])
        if outcome['state'] != 'VERIFIED_SIM':
            break
        allowed[level] = [receipt['object_id']]
        _record_first_receipt(root, level, receipt)
    completed = len(steps) == len(FIRST_LEVELS) and steps['AD']['state'] == 'VERIFIED_SIM'
    if completed:
        ad_plan = SnapshotPlan('AD', steps['AD']['object_id'], account_id=allowed['account_id'])
        readback = normalize_snapshot(ad_plan, *service.snapshot_factory(ad_plan).get(ad_plan.request()))
        if (readback['campaign_id'] != steps['CAMPAIGN']['object_id'] or readback['adset_id'] != steps['ADSET']['object_id']
                or readback['creative'] != {'id': steps['CREATIVE']['object_id']} or readback['status'] != 'PAUSED'):
            fail('FIRST_LAUNCH_AD_CHAIN_READBACK_MISMATCH')
    result = dict(status='FIRST_LAUNCH_SIM_VERIFIED' if completed else 'FIRST_LAUNCH_SIM_STOPPED',
                  mode='MOCK', steps=steps, ad_readback=readback, synthetic_send_attempts=service.send_attempts,
                  real_meta_requests=0, human_identity_verified=False, real_write_authorized=False,
                  activation_executed=False)
    if resume:
        resumed = _first_launch_root(root/'FIRST_LAUNCH_RESUMED.json')
        if completed and not resumed.exists():
            _write_new(root, resumed.name, result)
    else:
        _write_new(root, 'FIRST_LAUNCH.json', result)
    return result


def _prepare_first_ad(draft, material, asset_root, material_dir, *, clock):
    draft['context']['material_sha256'] = None
    if material_dir.exists():
        saved = load_draft(material_dir/'draft.json')
        expected = {**draft, 'context': {**draft['context'], 'material_sha256': saved['context']['material_sha256']}}
        if expected != saved:
            fail('FIRST_LAUNCH_MATERIAL_DRAFT_MISMATCH')
        evidence, findings = load_material_packet(material_dir, saved, clock=clock)
        # First-launch exports use the same fixed time for observation and review.
        material['observed_at'] = evidence['reviewed_at']
        if digest(material) != saved['context']['material_sha256']:
            fail('FIRST_LAUNCH_MATERIAL_DRAFT_MISMATCH')
        if findings:
            fail('FIRST_LAUNCH_MATERIAL_REVIEW_EXPIRED')
        return saved
    # Only the fresh observation changes. The original use dates and declared
    # fixture rights remain in force and are checked by the ordinary binder.
    check_media_files(material, asset_root)
    material['observed_at'] = clock().isoformat()
    export_material(material, asset_root, material_dir, draft, clock=clock)
    return load_draft(material_dir/'draft.json')
