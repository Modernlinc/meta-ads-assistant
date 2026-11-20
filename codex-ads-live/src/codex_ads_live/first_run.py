"""Portable offline installation rehearsal using the packaged CLI entry points."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import re
import subprocess
import sys
import time

from .journal import canonical, digest
from .local_paths import checked_path
from .onboarding import blank_preparation, reported_os_build
from .validation import InputError, aware_time, fail, object_only, strict_json


FIRST_RUN_SCHEMA = 'offline-first-run-receipt-v4'
PROJECT_DIRECTORY = '演练 项目'
PROJECT_SKILL_DIRECTORY = '.agents/skills/codex-ads-live'
PROJECT_INSTALL_RECEIPT = '.agents/codex-ads-live-install-receipt.json'
PYTHON_OPTIONS = ('-I','-B','-X','utf8')
SYNTHETIC_FORM = 'prep/synthetic-assets.json'
SYNTHETIC_ASSET_IDS = {'business_portfolio':'400','app':'200','ad_account':'100','facebook_page':'500'}
SYNTHETIC_ASSET_REFERENCE = 'EV_SYNTHETIC_FIRST_RUN'
SYNTHETIC_TARGETS = {'ACCOUNT':'100','PAGE':'500'}
ONBOARDING_STEPS = {'workspace-onboarding':'onboarding', 'workspace-restored-onboarding':'restored-onboarding'}
ONBOARDING_REPORT_FILES = ('ONBOARDING.json','ONBOARDING.md')
INSTALL_FLAGS = {'install-preview':('--preview',), 'install':(), 'install-repeat':(),
                 'install-readback':('--install-status',)}
MAPPED_BEFORE = 'approval/CRASH_BEFORE_SEND'
MAPPED_AFTER = 'approval/CRASH_AFTER_SEND'
MAPPED_READBACK_ARTIFACTS = (
    MAPPED_BEFORE+'/INTERRUPTED.json', MAPPED_AFTER+'/INTERRUPTED.json',
    MAPPED_BEFORE+'/service/snapshot-0000000001.json', MAPPED_AFTER+'/service/snapshot-0000000003.json',
)
MAPPED_READBACK_COMMANDS = {
    'approval-readback-before-send': ('mapped-reconcile-sim','--state-dir',MAPPED_BEFORE+'/ledger',
                                     '--service-dir',MAPPED_BEFORE+'/service','--action','SIM_ACTION_DEMO'),
    'approval-readback-after-send': ('mapped-reconcile-sim','--state-dir',MAPPED_AFTER+'/ledger',
                                    '--service-dir',MAPPED_AFTER+'/service','--action','SIM_ACTION_DEMO'),
    'approval-readback-reopen': ('mapped-status','--state-dir',MAPPED_AFTER+'/ledger'),
}
FIRST_RUN_ARTIFACTS = (
    'existing-project.txt',PROJECT_INSTALL_RECEIPT,
    '.agents/skills/codex-ads-live/MANIFEST.json','prep/preparation.json','prep-review/RECEIPT.json',SYNTHETIC_FORM,
    *(f'{folder}/{name}' for folder in ONBOARDING_STEPS.values() for name in (*ONBOARDING_REPORT_FILES,'RECEIPT.json')),
    'backup/config.json','report/REPORT.md','report/REPORT.json','report/RECEIPT.json','approval/RECEIPT.json',
    *MAPPED_READBACK_ARTIFACTS,
)
FIRST_RUN_PREFIX = (
    'source-preflight','install-preview','install','install-repeat','install-readback','installed-preflight','blank-preparation',
    'assess-preparation','workspace-init','workspace-reopen','workspace-prerequisite',
    'workspace-onboard-assets','workspace-default-account','workspace-default-page','workspace-onboarding',
    'workspace-backup','workspace-restore','workspace-restored-onboarding','monitor-demo','saved-report','approval-demo','approval-reopen',
    *MAPPED_READBACK_COMMANDS,
)
FIRST_RUN_CHECKS = tuple(f'worker-check-{attempt:02d}' for attempt in range(1,21))
FIRST_RUN_SUFFIX = ('worker-pause','worker-stop','worker-exit','worker-resume')
FIRST_RUN_EXCLUSIONS = ('host_discovery','native_credentials','real_meta','customer_acceptance')
EXPECTED_REJECTIONS = frozenset(('install-repeat','workspace-prerequisite','approval-readback-before-send'))


def _step_command(step, package, project, python):
    if step == 'source-preflight':
        script, cwd, args = package/'run.py', package, ('preflight',)
    elif step in INSTALL_FLAGS:
        script = package/'install.py'
        cwd = project if step == 'install-readback' else package
        args = ('--project', project, *INSTALL_FLAGS[step])
    else:
        script, cwd = project/PROJECT_SKILL_DIRECTORY/'run.py', project
        form = project/'prep/preparation.json'
        workspace = ('--workspace','.codex-ads-live/client')
        runtime = ('--state-dir','runtime')
        commands = {
            'installed-preflight': ('preflight',),
            'blank-preparation': ('prepare','--output','prep'),
            'assess-preparation': ('preparation-review',form,'--output','prep-review'),
            'workspace-init': ('workspace-init',*workspace,'--preparation',form),
            'workspace-reopen': ('workspace-show',*workspace),
            'workspace-prerequisite': ('workspace-plan',*workspace,'--task','ads-review','--last-complete-days','1',
                                       '--output','blocked-plan'),
            'workspace-onboard-assets': ('workspace-onboard',*workspace,'--preparation',project/SYNTHETIC_FORM),
            'workspace-default-account': ('workspace-default',*workspace,'--kind','ACCOUNT','--id',SYNTHETIC_TARGETS['ACCOUNT']),
            'workspace-default-page': ('workspace-default',*workspace,'--kind','PAGE','--id',SYNTHETIC_TARGETS['PAGE']),
            'workspace-onboarding': ('workspace-onboarding-report',*workspace,'--scope','ads-and-page',
                                     '--output',ONBOARDING_STEPS['workspace-onboarding']),
            'workspace-backup': ('workspace-backup',*workspace,'--output','backup'),
            'workspace-restore': ('workspace-restore','--workspace','restored','--backup-file','backup/config.json'),
            'workspace-restored-onboarding': ('workspace-onboarding-report','--workspace','restored','--scope','ads-and-page',
                                              '--output',ONBOARDING_STEPS['workspace-restored-onboarding']),
            'monitor-demo': ('monitor-demo','--include-review','--output','monitor'),
            'saved-report': ('monitor-report','--state-dir','monitor/monitor-state','--output','report'),
            'approval-demo': ('mapped-demo','--keep-interrupted','--output','approval'),
            'approval-reopen': ('mapped-status','--state-dir','approval/CREATE_AD/ledger'),
            **MAPPED_READBACK_COMMANDS,
            'worker-pause': ('pause',*runtime),
            'worker-stop': ('stop',*runtime),
            'worker-exit': ('start',*runtime,'--interval','0.1'),
            'worker-resume': ('resume',*runtime,'--interval','0.1','--cycles','2'),
        }
        args = ('status',*runtime) if step in FIRST_RUN_CHECKS else commands[step]
    return [python,*PYTHON_OPTIONS,str(script),*map(str,args)],cwd


def _accepted_step_exits(step):
    if step in FIRST_RUN_CHECKS:
        return (0,2)
    return (2,) if step in EXPECTED_REJECTIONS else (0,)


def _json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def _identity(path, root):
    data = path.read_bytes()
    return dict(path=path.relative_to(root).as_posix(), bytes=len(data), sha256=sha256(data).hexdigest())


def _expect(condition, code):
    if not condition:
        fail(code)


def _install_readback_matches(install, status, raw_receipt):
    return (status['schema']=='project-skill-install-status-v1' and status['status']=='PROJECT_INSTALL_VERIFIED'
            and status['host_discovery_verified'] is False and status['credentials_accessed'] is False
            and status['network_used'] is False
            and status['files_present']==dict(skill_directory=True,skill=True,manifest=True,install_receipt=True)
            and all(status[key]==install[key] for key in ('project','destination','receipt','manifest_sha256','version'))
            and status['install_receipt_sha256']==sha256(raw_receipt).hexdigest()
            and strict_json(raw_receipt.decode('utf-8'))==install)


def _workspace_reopened(initial, reopened):
    return (initial.get('mode')=='ONBOARDING' and bool(initial.get('client_id'))
            and reopened=={key:value for key,value in initial.items() if key!='mode'})


def _synthetic_preparation():
    form = blank_preparation()
    for key, identifier in SYNTHETIC_ASSET_IDS.items():
        form['assets'][key].update(id=identifier,ownership_evidence_ref=SYNTHETIC_ASSET_REFERENCE)
    return form


def _prepared_workspace_matches(proofs, backup):
    client_id = proofs['workspace-init']['client_id']
    profile_keys = ('client_id','assets','mappings','default_configured','app_configured','business_configured',
                    'credential_reference_configured','dispatchable')
    initial, account, page, restored = (proofs[name] for name in ('workspace-onboard-assets','workspace-default-account',
                                                                  'workspace-default-page','workspace-restore'))
    if any(row['client_id']!=client_id or row['assets']!={'ACCOUNT':1,'PAGE':1}
           or row['app_configured'] is not True or row['business_configured'] is not True
           or row['credential_reference_configured'] is not False or row['dispatchable'] is not False
           for row in (initial, account, page, restored)):
        return False
    if (initial['default_configured']!={'ACCOUNT':False,'PAGE':False}
            or account['default_configured']!={'ACCOUNT':True,'PAGE':False}
            or page['default_configured']!={'ACCOUNT':True,'PAGE':True}
            or any(page[key]!=restored[key] for key in profile_keys)):
        return False
    assets = {f'{kind}:{identifier}': dict(kind=kind,id=identifier,ownership_evidence_ref=SYNTHETIC_ASSET_REFERENCE)
              for kind,identifier in SYNTHETIC_TARGETS.items()}
    expected = dict(client_id=client_id,app_id=SYNTHETIC_ASSET_IDS['app'],
                    business_id=SYNTHETIC_ASSET_IDS['business_portfolio'],assets=assets,defaults=SYNTHETIC_TARGETS)
    return backup==dict(schema='client-config-backup-v1',config=expected,sha256=digest(expected))


def _onboarding_matches(folder, stdout, artifact_bytes, manifest):
    # The report, nested receipt and command result must describe the same two pending assets.
    receipt = strict_json(artifact_bytes(f'{folder}/RECEIPT.json').decode('utf-8'))
    files = []
    for name in ONBOARDING_REPORT_FILES:
        raw = artifact_bytes(f'{folder}/{name}')
        files.append(dict(path=name,bytes=len(raw),sha256=sha256(raw).hexdigest()))
    expected_receipt = dict(status='WORKSPACE_ONBOARDING_REPORT_WRITTEN',mode='LOCAL_RECORDED_EVIDENCE',files=files,
                            assessment='REVIEW_REQUIRED',real_connection_verified=False,live_action_authorized=False,
                            network_used=False,native_credential_accessed=False)
    local_only = ('real_connection_verified','live_action_authorized','network_used','native_credential_accessed')
    if receipt!=stdout or receipt!=expected_receipt or any(receipt[key] is not False for key in local_only):
        return False
    report = strict_json(artifact_bytes(f'{folder}/ONBOARDING.json').decode('utf-8'))
    if (report['schema']!='workspace-onboarding-report-v1' or report['scope']!='ads-and-page'
            or any(report[key]!=receipt[key] for key in ('mode','assessment'))
            or any(report[key] is not False for key in local_only)
            or report['customer_acceptance']!='NOT_ASSESSED' or report['live_and_insights_coverage']!='NOT_ASSESSED'
            or report['local_preflight']['manifest_sha256']!=manifest
            or report['local_preflight']['status']!='LOCAL_PREREQUISITES_PRESENT'
            or report['workspace']['app_configured'] is not True or report['workspace']['business_configured'] is not True
            or report['assets'].keys()!=SYNTHETIC_TARGETS.keys()):
        return False
    expected = {'ACCOUNT': ('CONNECT_EXISTING_APP','ads_read'), 'PAGE': ('VERIFY_PAGE_ROLES_AND_ACCESS','pages_read_engagement')}
    for kind, (reason, permission) in expected.items():
        row = report['assets'][kind]
        if (row['target_id']!=SYNTHETIC_TARGETS[kind] or row['mapped'] is not True
                or row['connection_evidence']!='NOT_RECORDED' or row['reason']!=reason
                or row['required_permissions']!=[permission] or row['last_recorded_at'] is not None):
            return False
    return True


def _saved_report_matches(report):
    return (report['mode']=='MOCK' and report['monitor_phase']=='STOPPED'
            and report['business_review']['ads']['analysis']['metrics']['spend']['value']=='15.00'
            and report['observations']['live_social_actions']['last_error']=='PERMISSION')


def _simulated_approval_matches(approval, ledger):
    ad = approval['scenarios']['CREATE_AD']
    return (approval['mode']==ledger['mode']=='MOCK' and type(approval['real_meta_requests']) is int
            and approval['real_meta_requests']==0 and approval['real_write_authorized'] is False
            and ledger['real_write_authorized'] is False and ad['state']=='VERIFIED_SIM'
            and ad['receipt']['configured_status']=='PAUSED' and ledger['actions']['SIM_ACTION_DEMO']==ad)


def _mapped_readback_matches(approval, pending, recovered, reopened, artifact):
    from .mapped_actions import validate_receipt

    before,after,before_service,after_service = (artifact(name) for name in MAPPED_READBACK_ARTIFACTS)
    common = {'action_id':'SIM_ACTION_DEMO','readback_only':True}
    before_id,after_id = before['execution_id'],after['execution_id']
    expected_pending = dict(before,**common,readback_outcome='UNVERIFIED')
    if 'readback_reason' in pending:
        expected_pending['readback_reason'] = 'MAPPED_NO_CORRELATED_RECEIPT'
    if (before!=approval['scenarios']['CRASH_BEFORE_SEND'] or after!=approval['scenarios']['CRASH_AFTER_SEND']
            or any(item['state']!='UNKNOWN' or item['receipt'] is not None or item['mode']!='MOCK'
                   or item['real_write_authorized'] is not False or item['human_identity_verified'] is not False
                   or type(item['real_meta_requests']) is not int or item['real_meta_requests']!=0
                   for item in (before,after))
            or before_id==after_id
            or not all(re.fullmatch(r'SIM_EXEC_[A-F0-9]{32}',value) for value in (before_id,after_id))
            or pending['readback_only'] is not True or recovered['readback_only'] is not True
            or pending!=expected_pending):
        return False
    receipt = recovered['receipt']
    confirmed = dict(after,state='VERIFIED_SIM',receipt=receipt,last_error=None)
    if (recovered!=dict(confirmed,**common,readback_outcome='VERIFIED_SIM')
            or reopened['mode']!='MOCK' or reopened['real_write_authorized'] is not False
            or reopened['actions']!={'SIM_ACTION_DEMO':confirmed}):
        return False
    for envelope in (before_service,after_service):
        if (envelope['sha256']!=digest(envelope['payload'])
                or envelope['payload']['schema']!='mapped-synthetic-service-v2'):
            return False
    service = after_service['payload']
    if before_service['payload']['receipts']!={} or set(service['receipts'])!={after_id}:
        return False
    committed = service['receipts'][after_id]
    node = service['nodes'][committed['node_key']]
    try:
        validate_receipt(receipt,dict(packet=committed['packet'],execution_id=after_id))
    except InputError:
        return False
    return (committed['receipt']==receipt and receipt['execution_id']==after_id
            and receipt['operation']=='CREATE_AD' and receipt['review_sha256']==after['review_sha256']
            and receipt['configured_status']==node['data']['status']=='PAUSED'
            and receipt['after_data_sha256']==digest(node['data'])
            and receipt['after_configuration_sha256']==digest(node['configuration']))


def _worker_resumed(paused, resumed):
    return (paused['phase']=='PAUSED' and resumed['phase']=='STOPPED'
            and type(paused['cycle_count']) is int and type(resumed['cycle_count']) is int
            and resumed['cycle_count']==paused['cycle_count']+2
            and bool(resumed['last_successful_sync']['ads']) and resumed['last_successful_sync']['live'] is None)


class FirstRun:
    def __init__(self, package, root):
        self.package = package
        self.root = root
        self.project = root/PROJECT_DIRECTORY
        self.skill = self.project/PROJECT_SKILL_DIRECTORY
        self.command_dir = root/'commands'
        self.steps = []
        self.current_step = 'START'
        self.artifacts = []
        self.manifest_sha256 = None

    def prepare(self):
        self.project.mkdir()
        self.command_dir.mkdir()
        self.marker = self.project/'existing-project.txt'
        with self.marker.open('x', encoding='utf-8') as stream:
            stream.write('Synthetic existing project file.\n')
        self.marker_before = _identity(self.marker,self.root)

    def record(self, step, command, cwd, exit_code, stdout, stderr):
        path = self.command_dir/f'{len(self.steps)+1:02d}-{step}.json'
        _json(path,dict(arguments=command,cwd=str(cwd),exit_code=exit_code,stdout=stdout,stderr=stderr))
        self.steps.append(dict(step=step,exit_code=exit_code,evidence=_identity(path,self.root)))

    def invoke(self, step, *, timeout=60):
        self.current_step = step
        command,cwd = _step_command(step,self.package,self.project,sys.executable)
        try:
            result = subprocess.run(command,cwd=cwd,capture_output=True,encoding='utf-8',errors='replace',timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stdout,stderr = (part.decode('utf-8',errors='replace') if part is not None else ''
                             for part in (exc.stdout,exc.stderr))
            self.record(step,command,cwd,None,stdout,stderr)
            fail('FIRST_RUN_STEP_TIMED_OUT')
        self.record(step,command,cwd,result.returncode,result.stdout,result.stderr)
        _expect(result.returncode in _accepted_step_exits(step),'FIRST_RUN_STEP_EXIT_CODE')
        value = json.loads(result.stdout)
        _expect(type(value) is dict,'FIRST_RUN_STEP_RESPONSE')
        return value

    def install(self):
        initial = self.invoke('source-preflight')
        self.manifest_sha256 = initial.get('manifest_sha256')
        _expect(initial.get('status')=='LOCAL_PREREQUISITES_PRESENT'
                and re.fullmatch('[a-f0-9]{64}',self.manifest_sha256 or ''),'FIRST_RUN_PREREQUISITES')
        preview = self.invoke('install-preview')
        _expect(preview.get('status')=='INSTALL_PLAN_READY' and Path(preview['destination'])==self.skill
                and not (self.project/'.agents').exists(),'FIRST_RUN_PREVIEW')
        made = self.invoke('install')
        _expect(made.get('status')=='PROJECT_SKILL_INSTALLED_HASH_VERIFIED'
                and made['manifest_sha256']==self.manifest_sha256,'FIRST_RUN_INSTALL')
        duplicate = self.invoke('install-repeat')
        _expect(duplicate.get('error_code')=='PROJECT_SKILL_ALREADY_EXISTS','FIRST_RUN_INSTALL_REPEAT')
        readback = self.invoke('install-readback')
        _expect(_install_readback_matches(made,readback,(self.project/PROJECT_INSTALL_RECEIPT).read_bytes()),
                'FIRST_RUN_INSTALL_READBACK')
        installed = self.invoke('installed-preflight')
        _expect(installed.get('status')=='LOCAL_PREREQUISITES_PRESENT'
                and installed['manifest_sha256']==self.manifest_sha256,'FIRST_RUN_INSTALLED_PREREQUISITES')

    def workspace(self):
        self.invoke('blank-preparation')
        form = self.project/'prep/preparation.json'
        before = _identity(form,self.root)
        looked = self.invoke('assess-preparation')
        _expect(looked.get('status')=='PREPARATION_ASSESSED','FIRST_RUN_PREPARATION')
        initial = self.invoke('workspace-init')
        reopened = self.invoke('workspace-reopen')
        _expect(_workspace_reopened(initial,reopened),'FIRST_RUN_WORKSPACE_REOPEN')
        blocked = self.invoke('workspace-prerequisite')
        _expect(blocked['workspace_route']['reason']=='COMPLETE_APP_AND_BUSINESS_MAPPING'
                and blocked['network_used'] is False and not (self.project/'blocked-plan').exists(),'FIRST_RUN_WORKSPACE_GATE')

        _json(self.project/SYNTHETIC_FORM,_synthetic_preparation())
        proofs = {'workspace-init':initial}
        def artifact_bytes(name): return (self.project/name).read_bytes()
        steps = ('workspace-onboard-assets','workspace-default-account','workspace-default-page','workspace-onboarding',
                 'workspace-backup','workspace-restore','workspace-restored-onboarding')
        for step in steps:
            proofs[step] = self.invoke(step)
            if step in ONBOARDING_STEPS:
                _expect(_onboarding_matches(ONBOARDING_STEPS[step],proofs[step],artifact_bytes,self.manifest_sha256),
                        'FIRST_RUN_WORKSPACE_ONBOARDING')
        backup = strict_json(artifact_bytes('backup/config.json').decode('utf-8'))
        _expect(proofs['workspace-backup']==dict(status='NON_SECRET_BACKUP_CREATED',credentials_included=False)
                and _prepared_workspace_matches(proofs,backup) and _identity(form,self.root)==before,
                'FIRST_RUN_WORKSPACE_RESTORE')

    def reports(self):
        self.invoke('monitor-demo')
        self.invoke('saved-report')
        report = json.loads((self.project/'report/REPORT.json').read_text(encoding='utf-8'))
        _expect(_saved_report_matches(report),'FIRST_RUN_SAVED_REPORT')
        approval = self.invoke('approval-demo')
        ledger = self.invoke('approval-reopen')
        _expect(_simulated_approval_matches(approval,ledger),'FIRST_RUN_SIMULATED_APPROVAL')
        initial_services = {path:_identity(path,self.root) for case in (MAPPED_BEFORE,MAPPED_AFTER)
                            for path in (self.project/case/'service').glob('snapshot-*.json')}
        pending,recovered,reopened = (self.invoke(step) for step in MAPPED_READBACK_COMMANDS)
        services = {path:_identity(path,self.root) for case in (MAPPED_BEFORE,MAPPED_AFTER)
                    for path in (self.project/case/'service').glob('snapshot-*.json')}
        def artifact(name): return strict_json((self.project/name).read_text(encoding='utf-8'))
        _expect(initial_services==services and _mapped_readback_matches(approval,pending,recovered,reopened,artifact),
                'FIRST_RUN_MAPPED_READBACK')

    def runtime(self):
        self.current_step = 'worker-start'
        command,cwd = _step_command('worker-exit',self.package,self.project,sys.executable)
        worker = subprocess.Popen(command,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  encoding='utf-8',errors='replace')
        worker_recorded = False
        try:
            for step in FIRST_RUN_CHECKS:
                observed = self.invoke(step,timeout=5)
                if observed.get('phase')=='RUNNING' and observed.get('cycle_count',0)>0:
                    break
                _expect(worker.poll() is None,'FIRST_RUN_WORKER_EARLY_EXIT')
                time.sleep(0.1)
            else:
                fail('FIRST_RUN_WORKER_NOT_READY')
            paused = self.invoke('worker-pause')
            _expect(paused['phase']=='PAUSED','FIRST_RUN_WORKER_NOT_PAUSED')
            stopped = self.invoke('worker-stop')
            _expect(stopped['phase']=='STOPPED','FIRST_RUN_WORKER_NOT_STOPPED')
            self.current_step = 'worker-exit'
            stdout,stderr = worker.communicate(timeout=10)
            self.record('worker-exit',command,self.project,worker.returncode,stdout,stderr)
            worker_recorded = True
            _expect(worker.returncode==0 and json.loads(stdout)['phase']=='STOPPED','FIRST_RUN_WORKER_EXIT')
        finally:
            if not worker_recorded:
                if worker.poll() is None:
                    worker.terminate()
                try:
                    stdout,stderr = worker.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    stdout,stderr = worker.communicate(timeout=5)
                self.record('worker-cleanup',command,self.project,worker.returncode,stdout,stderr)
        resumed = self.invoke('worker-resume')
        _expect(_worker_resumed(paused,resumed),'FIRST_RUN_WORKER_RECOVERY')

    def finalize(self):
        self.current_step = 'artifact-readback'
        _expect(_identity(self.marker,self.root)==self.marker_before,'FIRST_RUN_ORIGINAL_FILE_CHANGED')
        _expect(_identity(self.skill/'MANIFEST.json',self.root)['sha256']==self.manifest_sha256,
                'FIRST_RUN_INSTALLED_MANIFEST_CHANGED')
        receipt = self.project/'report/RECEIPT.json'
        for entry in json.loads(receipt.read_text(encoding='utf-8'))['files']:
            path = receipt.parent/entry['path']
            expected = dict(entry,path=path.relative_to(self.root).as_posix())
            _expect(_identity(path,self.root)==expected,'FIRST_RUN_REPORT_DIGEST')
        self.artifacts = [_identity(self.project/name,self.root) for name in FIRST_RUN_ARTIFACTS]


def run_first_run(package, output):
    root = checked_path(output,link_error='FIRST_RUN_OUTPUT_LINK',volume_error='FIRST_RUN_VOLUME_NOT_MOUNTED')
    if root.is_relative_to(package):
        fail('FIRST_RUN_OUTPUT_INSIDE_SOURCE')
    root.mkdir(parents=True,exist_ok=False,mode=0o700)
    journey = FirstRun(package,root)
    started = datetime.now(timezone.utc).isoformat()
    status, error = 'PASSED',None
    try:
        journey.prepare()
        journey.install()
        journey.workspace()
        journey.reports()
        journey.runtime()
        journey.finalize()
    except (InputError, OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        status = 'FAILED'
        error = str(exc) if isinstance(exc,InputError) else 'FIRST_RUN_EVIDENCE_OR_IO_FAILURE'
    result = dict(schema=FIRST_RUN_SCHEMA,status=status,
                  failed_step=journey.current_step if error else None,error_code=error,started_at=started,
                  operating_system=platform.system(),system_release=platform.release(),system_version=platform.version(),
                  architecture=platform.machine(),python=platform.python_version(),
                  manifest_sha256=journey.manifest_sha256,project_path=str(journey.project),
                  steps=journey.steps,artifacts=journey.artifacts,
                  scope='OFFLINE_ISOLATED_SYNTHETIC_REHEARSAL',host_discovery='NOT_RUN',
                  native_credentials='NOT_RUN',real_meta='NOT_RUN',customer_acceptance='NOT_RUN')
    path = root/'receipt.json'
    _json(path,result)
    return dict(status=status,receipt=str(path),steps=len(journey.steps),failed_step=result['failed_step'],error_code=error,
                scope=result['scope'],host_discovery='NOT_RUN',real_meta='NOT_RUN')


def _review_bytes(root, name, limit):
    path = checked_path(root/name,link_error='FIRST_RUN_REVIEW_PATH_LINK',volume_error='FIRST_RUN_VOLUME_NOT_MOUNTED')
    if not path.is_file(): fail('FIRST_RUN_REVIEW_FILE_MISSING')
    with path.open('rb') as stream:
        data = stream.read(limit+1)
    if len(data)>limit: fail('FIRST_RUN_REVIEW_FILE_TOO_LARGE')
    return data


def _checked_evidence(root, entry, name):
    keys = ('path','bytes','sha256')
    object_only(entry,keys,keys)
    if entry['path'] != name: fail('FIRST_RUN_REVIEW_EVIDENCE_PATH')
    raw = _review_bytes(root,name,2_000_000)
    if type(entry['bytes']) is not int or entry['bytes']!=len(raw) or entry['sha256']!=sha256(raw).hexdigest():
        fail('FIRST_RUN_REVIEW_EVIDENCE_DIGEST')
    return raw


def _review_command_bindings(commands, project_path, system):
    """Compare paths in the recorded platform's syntax without opening them."""
    error = 'FIRST_RUN_REVIEW_COMMAND'
    path_type = PureWindowsPath if system == 'Windows' else PurePosixPath
    first = commands['source-preflight']
    _expect(type(project_path) is str and len(first['arguments'])>=6,error)
    project, package = path_type(project_path), path_type(first['cwd'])
    python = first['arguments'][0]
    _expect(project.is_absolute() and project.name==PROJECT_DIRECTORY and package.is_absolute()
            and path_type(python).is_absolute() and not project.parent.is_relative_to(package)
            and not package.is_relative_to(project.parent),error)
    for step, record in commands.items():
        command_step = 'worker-exit' if step=='worker-cleanup' else step
        expected, cwd = _step_command(command_step,package,project,python)
        _expect(record['arguments']==expected and record['cwd']==str(cwd),error)
    return project


def _review_failure_progress(receipt, commands):
    """Check a partial run, including failures before a command could be saved."""
    error = 'FIRST_RUN_REVIEW_FAILURE_PROGRESS'
    failed, recorded_error = receipt['failed_step'], receipt['error_code']
    _expect(type(recorded_error) is str and re.fullmatch('[A-Z0-9_]{1,80}',recorded_error),error)
    observed = tuple(commands)
    if 'worker-cleanup' in commands:
        _expect(observed[-1]=='worker-cleanup' and 'worker-exit' not in commands
                and failed in (*FIRST_RUN_CHECKS,'worker-pause','worker-stop','worker-exit'),error)
        observed = observed[:-1]

    internal = ('START','worker-start','artifact-readback')
    for check_count in range(1,len(FIRST_RUN_CHECKS)+1):
        route = ('START',*FIRST_RUN_PREFIX,'worker-start',*FIRST_RUN_CHECKS[:check_count],
                 *FIRST_RUN_SUFFIX,'artifact-readback')
        if failed not in route:
            continue
        prior = tuple(step for step in route[:route.index(failed)] if step not in internal)
        if observed==prior or (failed not in internal and observed==(*prior,failed)):
            break
    else:
        fail(error)

    for step in prior:
        _expect(commands[step]['exit_code'] in _accepted_step_exits(step),error)
    if recorded_error=='FIRST_RUN_STEP_EXIT_CODE':
        _expect(failed in commands and type(commands[failed]['exit_code']) is int
                and commands[failed]['exit_code'] not in _accepted_step_exits(failed),error)
    if recorded_error=='FIRST_RUN_STEP_TIMED_OUT':
        _expect(failed in commands and commands[failed]['exit_code'] is None,error)
    if 'source-preflight' in prior:
        proof = strict_json(commands['source-preflight']['stdout'])
        _expect(type(proof) is dict and proof.get('status')=='LOCAL_PREREQUISITES_PRESENT'
                and receipt['manifest_sha256'] is not None and proof.get('manifest_sha256')==receipt['manifest_sha256'],error)
    if commands:
        _review_command_bindings(commands,receipt['project_path'],receipt['operating_system'])
    return prior[-1] if prior else None


def _review_successful_outcomes(proofs, artifacts, manifest, project):
    """Compare recorded outputs and artifacts without following paths in their contents."""
    def artifact(name):
        return strict_json(artifacts[name].decode('utf-8'))

    def require(condition):
        _expect(condition,'FIRST_RUN_REVIEW_OUTCOME')

    def file_entry(name, raw):
        return dict(path=name,bytes=len(raw),sha256=sha256(raw).hexdigest())

    try:
        preview = proofs['install-preview']
        require(preview['status']=='INSTALL_PLAN_READY' and preview['manifest_sha256']==manifest)
        planned_paths = dict(project=str(project),destination=str(project/PROJECT_SKILL_DIRECTORY),
                             receipt=str(project/PROJECT_INSTALL_RECEIPT))
        installed = proofs['install']
        require(all(preview[field]==installed[field]==expected for field,expected in planned_paths.items()))
        require(proofs['install-repeat']['error_code']=='PROJECT_SKILL_ALREADY_EXISTS')
        require(_install_readback_matches(installed,proofs['install-readback'],artifacts[PROJECT_INSTALL_RECEIPT]))
        preparation_sha = digest(artifact('prep/preparation.json'))
        preparation = artifact('prep-review/RECEIPT.json')
        assessment = proofs['assess-preparation']
        require(assessment['status']==preparation['status']=='PREPARATION_ASSESSED'
                and assessment['input_sha256']==preparation['input_sha256']==preparation_sha)

        initial = proofs['workspace-init']
        require(_workspace_reopened(initial,proofs['workspace-reopen']))
        blocked = proofs['workspace-prerequisite']
        require(blocked['workspace_route']['reason']=='COMPLETE_APP_AND_BUSINESS_MAPPING'
                and blocked['network_used'] is False)
        require(artifact(SYNTHETIC_FORM)==_synthetic_preparation())
        require(proofs['workspace-backup']['status']=='NON_SECRET_BACKUP_CREATED'
                and proofs['workspace-backup']['credentials_included'] is False
                and _prepared_workspace_matches(proofs,artifact('backup/config.json')))
        for step, folder in ONBOARDING_STEPS.items():
            require(_onboarding_matches(folder,proofs[step],artifacts.__getitem__,manifest))

        require(_saved_report_matches(artifact('report/REPORT.json')))
        report_receipt = artifact('report/RECEIPT.json')
        require(report_receipt==proofs['saved-report']
                and report_receipt['status']=='BOUNDED_OBSERVATION_REPORT_WRITTEN'
                and report_receipt['files']==[file_entry(name,artifacts['report/'+name])
                                             for name in ('REPORT.json','REPORT.md')])
        approval = proofs['approval-demo']
        require(_simulated_approval_matches(approval,proofs['approval-reopen']))
        approval_receipt = artifact('approval/RECEIPT.json')
        demo_bytes = (canonical(approval)+'\n').encode('utf-8')
        require(approval_receipt['mode']=='MOCK' and type(approval_receipt['real_meta_requests']) is int
                and approval_receipt['real_meta_requests']==0
                and file_entry('RESULT.json',demo_bytes) in approval_receipt['files'])
        require(all(file_entry(name.removeprefix('approval/'),artifacts[name]) in approval_receipt['files']
                    for name in MAPPED_READBACK_ARTIFACTS))
        require(_mapped_readback_matches(approval,proofs['approval-readback-before-send'],
                                        proofs['approval-readback-after-send'],proofs['approval-readback-reopen'],artifact))
        require(proofs['worker-stop']['phase']==proofs['worker-exit']['phase']=='STOPPED'
                and _worker_resumed(proofs['worker-pause'],proofs['worker-resume']))
    except (AttributeError, KeyError, TypeError):
        fail('FIRST_RUN_REVIEW_OUTCOME')


def review_first_run(directory, expected_manifest):
    """Read a transferred rehearsal without launching its commands or trusting device claims."""
    root = checked_path(directory,link_error='FIRST_RUN_REVIEW_PATH_LINK',volume_error='FIRST_RUN_VOLUME_NOT_MOUNTED')
    raw = _review_bytes(root,'receipt.json',160_000)
    receipt = strict_json(raw.decode('utf-8'))
    keys = ('schema','status','failed_step','error_code','started_at','operating_system','system_release',
            'system_version','architecture','python','manifest_sha256','project_path','steps','artifacts','scope',
            *FIRST_RUN_EXCLUSIONS)
    object_only(receipt,keys,keys)
    status = receipt['status']
    if (receipt['schema']!=FIRST_RUN_SCHEMA or status not in ('PASSED','FAILED')
            or receipt['scope']!='OFFLINE_ISOLATED_SYNTHETIC_REHEARSAL'
            or any(receipt[key]!='NOT_RUN' for key in FIRST_RUN_EXCLUSIONS)):
        fail('FIRST_RUN_REVIEW_SCHEMA')
    aware_time(receipt['started_at'])
    passed = status=='PASSED'
    build = reported_os_build(receipt['operating_system'],receipt['system_release'],receipt['system_version'])
    build_checked = False
    manifest = receipt['manifest_sha256']
    if manifest!=expected_manifest and (passed or manifest is not None):
        fail('FIRST_RUN_REVIEW_PACKAGE_MISMATCH')
    steps, artifacts = receipt['steps'], receipt['artifacts']
    if (type(steps) is not list or len(steps)>len(FIRST_RUN_PREFIX)+len(FIRST_RUN_CHECKS)+len(FIRST_RUN_SUFFIX)
            or type(artifacts) is not list
            or len(artifacts)!=(len(FIRST_RUN_ARTIFACTS) if passed else 0)):
        fail('FIRST_RUN_REVIEW_CHECKLIST')
    labels = (*FIRST_RUN_PREFIX,*FIRST_RUN_CHECKS,*FIRST_RUN_SUFFIX,'worker-cleanup')
    observed = []
    proofs = {}
    commands = {}
    last_check = None
    prior_command = None
    for index,step in enumerate(steps,1):
        step_keys = ('step','exit_code','evidence')
        object_only(step,step_keys,step_keys)
        name, code = step['step'], step['exit_code']
        if (name not in labels or name in observed or type(code) is not int and (code is not None or passed)):
            fail('FIRST_RUN_REVIEW_CHECKLIST')
        observed.append(name)
        entry = _checked_evidence(root,step['evidence'],f'commands/{index:02d}-{name}.json')
        command = strict_json(entry.decode('utf-8'),max_chars=2_000_000)
        command_keys = ('arguments','cwd','exit_code','stdout','stderr')
        object_only(command,command_keys,command_keys)
        if (type(command['exit_code']) is not type(code) or command['exit_code']!=code
                or type(command['arguments']) is not list or any(type(arg) is not str for arg in command['arguments'])
                or any(type(command[key]) is not str for key in ('cwd','stdout','stderr'))):
            fail('FIRST_RUN_REVIEW_COMMAND')
        commands[name] = command
        if not passed: continue
        if code not in _accepted_step_exits(name): fail('FIRST_RUN_REVIEW_CHECKLIST')
        proof = strict_json(command['stdout'])
        if type(proof) is not dict: fail('FIRST_RUN_REVIEW_OUTCOME')
        proofs[name] = proof
        if name in ('source-preflight','install','installed-preflight'):
            checkpoint = 'PROJECT_SKILL_INSTALLED_HASH_VERIFIED' if name=='install' else 'LOCAL_PREREQUISITES_PRESENT'
            if proof.get('status')!=checkpoint or proof.get('manifest_sha256')!=manifest:
                fail('FIRST_RUN_REVIEW_PACKAGE_MISMATCH')
        if name in FIRST_RUN_CHECKS:
            last_check = proof
    if passed:
        checks = observed[len(FIRST_RUN_PREFIX):-len(FIRST_RUN_SUFFIX)]
        if (not 1<=len(checks)<=len(FIRST_RUN_CHECKS)
                or tuple(observed)!=(*FIRST_RUN_PREFIX,*FIRST_RUN_CHECKS[:len(checks)],*FIRST_RUN_SUFFIX)
                or type(last_check) is not dict or last_check.get('phase')!='RUNNING'
                or type(last_check.get('cycle_count')) is not int or last_check['cycle_count']<=0
                or receipt['failed_step'] is not None or receipt['error_code'] is not None):
            fail('FIRST_RUN_REVIEW_CHECKLIST')
        runtime_fields = {'operating_system':'system', 'system_release':'system_release',
                          'architecture':'architecture', 'python':'python'}
        if (receipt['operating_system'] not in ('Darwin','Windows')
                or any(type(receipt[field]) is not str or not receipt[field]
                       or any(proofs[step].get(preflight_field)!=receipt[field]
                              for step in ('source-preflight','installed-preflight'))
                       for field,preflight_field in runtime_fields.items())):
            fail('FIRST_RUN_REVIEW_RUNTIME_MISMATCH')
        preflight_builds = [proofs[step] for step in ('source-preflight','installed-preflight')]
        if any('os_build_sha256' in proof for proof in preflight_builds):
            expected_build = build['recorded_os_build_sha256']
            if expected_build is None or any(proof.get('os_build_sha256')!=expected_build for proof in preflight_builds):
                fail('FIRST_RUN_REVIEW_OS_BUILD_MISMATCH')
            build_checked = True
        project = _review_command_bindings(commands,receipt['project_path'],receipt['operating_system'])
        checked = {}
        for entry,name in zip(artifacts,FIRST_RUN_ARTIFACTS):
            content = _checked_evidence(root,entry,PROJECT_DIRECTORY+'/'+name)
            if name=='.agents/skills/codex-ads-live/MANIFEST.json' and sha256(content).hexdigest()!=manifest:
                fail('FIRST_RUN_REVIEW_PACKAGE_MISMATCH')
            checked[name] = content
        _review_successful_outcomes(proofs,checked,manifest,project)
    else:
        prior_command = _review_failure_progress(receipt,commands)
    system = receipt['operating_system']
    system = system if system in ('Darwin','Windows','Linux') else 'OTHER_OR_UNKNOWN'
    machine = receipt['architecture']
    machine = machine if machine in ('arm64','ARM64','aarch64','x86_64','AMD64','x86','i386','i686') else 'OTHER_OR_UNKNOWN'
    python = receipt['python']
    python = python if type(python) is str and re.fullmatch(r'3\.[0-9]{1,3}\.[0-9]{1,3}',python) else 'UNKNOWN'
    return dict(status='FIRST_RUN_EVIDENCE_CHECKED', recorded_result=status, receipt_sha256=sha256(raw).hexdigest(),
                recorded_failed_step=receipt['failed_step'],recorded_error_code=receipt['error_code'],
                recorded_prior_command=prior_command,
                package_binding='MATCHED_MANIFEST' if manifest is not None else 'UNAVAILABLE_DURING_FAILURE',
                manifest_sha256=manifest, checked_steps=len(steps), checked_artifacts=len(artifacts),
                reported_system=system, reported_architecture=machine, reported_python=python,
                recorded_preflights_match=passed,recorded_build_preflights_match=build_checked,
                device_identity_verified=False, **build, **{key:'NOT_RUN' for key in FIRST_RUN_EXCLUSIONS})
