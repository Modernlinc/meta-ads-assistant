"""Copy or stage this project-scoped local skill.

The upgrade path restores only portable settings into a separate project.
No network, dependency install, global config edits, overwrite or deletion.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import stat
import sys
import unicodedata


class InstallError(ValueError):pass


class InstallArgumentParser(argparse.ArgumentParser):
    """Keep the installer usable before its source payload is verified."""

    def error(self, _message):
        result = {'status': 'REJECTED', 'error_code': 'CLI_ARGUMENTS_REJECTED',
                  'help_command': 'install.py --help'}
        self.exit(2, json.dumps(result) + '\n')


UPGRADE_PLAN_FIELDS = ('schema','previous_project','new_project','previous_workspace','new_workspace',
                       'previous_manifest_sha256','new_manifest_sha256','previous_version','new_version',
                       'previous_workspace_schema','portable_config_sha256','staged_receipt')
WINDOWS_DEVICE_NAMES = {'CON','PRN','AUX','NUL',
                        *(prefix+digit for prefix in ('COM','LPT') for digit in '123456789¹²³')}


def mounted(path):
    if os.name=='posix' and len(path.parts)>2 and path.parts[1]=='Volumes':
        if not os.path.ismount(Path(*path.parts[:3])):raise InstallError('INSTALL_VOLUME_NOT_MOUNTED')
    if os.name=='nt' and not Path(path.anchor).is_dir():raise InstallError('INSTALL_DRIVE_UNAVAILABLE')


def local_path(value):
    path=Path(value).absolute();mounted(path)
    for part in (path,*path.parents):
        if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):
            raise InstallError('INSTALL_PATH_LINK')
    path=path.resolve();mounted(path);return path


def portable_name(name):
    return unicodedata.normalize('NFC',name).casefold()


def new_path(path,code):
    if path.exists() or path.is_symlink():raise InstallError(code)
    if path.parent.is_dir() and any(portable_name(p.name)==portable_name(path.name) for p in path.parent.iterdir()):
        raise InstallError(code)


def strict_manifest(text):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise InstallError('MANIFEST_DUPLICATE_KEY')
            value[key]=item
        return value
    def constant(_):raise InstallError('MANIFEST_NONFINITE')
    return json.loads(text,object_pairs_hook=pairs,parse_constant=constant)


def read_regular_file(path,limit,invalid_code,size_code):
    if not path.is_file():
        raise InstallError(invalid_code)
    flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
    with os.fdopen(os.open(path,flags),'rb') as stream:
        observed=os.fstat(stream.fileno())
        if not stat.S_ISREG(observed.st_mode):
            raise InstallError(invalid_code)
        if observed.st_size>limit:
            raise InstallError(size_code)
        data=stream.read(limit+1)
    if len(data)>limit:
        raise InstallError(size_code)
    return data


def payload_tree_matches(source,entries,tree):
    declared={Path(row['path']) for row in entries if row['path'].startswith(tree+'/')}
    if not declared:
        return False
    directories={parent for path in declared for parent in path.parents if parent.parts}
    for directory in directories:
        for path in (source/directory).iterdir():
            relative=path.relative_to(source)
            if relative in declared or relative in directories:
                continue
            if path.is_symlink() or path.is_junction():
                return False
            if (path.name=='__pycache__' and path.is_dir()
                    and all(item.suffix=='.pyc' and not item.is_symlink() and item.is_file() for item in path.iterdir())):
                continue
            if path.name in ('.DS_Store','Thumbs.db','desktop.ini') and path.is_file():
                continue
            return False
    return True


def verify_source(source):
    source=local_path(source)
    manifest_path=source/"MANIFEST.json"
    if manifest_path.is_symlink():raise InstallError("MANIFEST_SYMLINK")
    manifest=read_regular_file(manifest_path,1_000_000,'MANIFEST_NOT_REGULAR_FILE','MANIFEST_SIZE')
    data=strict_manifest(manifest.decode('utf-8'))
    if type(data) is not dict or set(data)!={'package','version','production_ready','status','files'}:raise InstallError('MANIFEST_SCHEMA')
    if data.get("package")!="codex-ads-live" or data.get("production_ready") is not False:raise InstallError("MANIFEST_IDENTITY")
    if data['status']!='DEVELOPMENT_CANDIDATE' or type(data['version']) is not str or not re.fullmatch(r'[0-9][A-Za-z0-9.+-]{0,63}',data['version']):raise InstallError('MANIFEST_VERSION')
    entries=data.get("files")
    if type(entries) is not list or not entries or len(entries)>512:raise InstallError("MANIFEST_FILES")
    seen=set();portable_paths={};total_bytes=0
    for entry in entries:
        if type(entry) is not dict or set(entry)!={'path','bytes','sha256'}:raise InstallError("MANIFEST_ENTRY")
        if type(entry['bytes']) is not int or not 0<=entry['bytes']<=20_000_000:raise InstallError("MANIFEST_BYTES")
        if type(entry['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}',entry['sha256']):raise InstallError("MANIFEST_HASH")
        total_bytes+=entry['bytes']
        if total_bytes>100_000_000:raise InstallError("MANIFEST_PAYLOAD_BUDGET")
        name=entry.get("path")
        if type(name) is not str or "\\" in name or ":" in name:raise InstallError("MANIFEST_PATH")
        relative=PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or name in seen:raise InstallError("MANIFEST_PATH")
        if name!=relative.as_posix():raise InstallError("MANIFEST_PORTABLE_PATH")
        parts=relative.parts
        portable_parts=tuple(portable_name(part) for part in parts)
        if portable_parts and portable_parts[0]==portable_name('MANIFEST.json'):
            raise InstallError('MANIFEST_SELF_ENTRY')
        for index,part in enumerate(parts,1):
            stem=part.split('.')[0].upper()
            if (part.endswith((' ','.')) or any(ord(c)<32 or c in '<>:"|?*' for c in part)
                or stem in WINDOWS_DEVICE_NAMES):
                raise InstallError("MANIFEST_PORTABLE_PATH")
            node=(parts[:index],index==len(parts))
            if portable_paths.setdefault(portable_parts[:index],node)!=node:
                raise InstallError("MANIFEST_PORTABLE_PATH")
        if relative.suffix not in (".py",".md",".json",".png",".html",".txt") and name!="assets/iana-timezones/tzif.zip":
            raise InstallError("MANIFEST_TYPE")
        seen.add(name)
    for entry in entries:
        path=source.joinpath(*PurePosixPath(entry['path']).parts)
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):raise InstallError("SOURCE_PATH_ESCAPE")
        for parent in path.parents:
            if parent==source:break
            if parent.is_symlink() or (hasattr(parent,'is_junction') and parent.is_junction()):raise InstallError("SOURCE_SYMLINK_PARENT")
        contents=read_regular_file(path,entry['bytes'],'SOURCE_NOT_REGULAR_FILE','SOURCE_CHECKSUM')
        if len(contents)!=entry['bytes'] or hashlib.sha256(contents).hexdigest()!=entry['sha256']:
            raise InstallError("SOURCE_CHECKSUM")
    required={"SKILL.md","run.py","install.py","src/codex_ads_live/__main__.py"}
    if "src/codex_ads_live/timezones.py" in seen:
        required.update("assets/iana-timezones/"+name for name in
                        ("tzif.zip","PROVENANCE.json","LICENSE.txt","LICENSE_APACHE.txt"))
    if not required<=seen:raise InstallError("SOURCE_INCOMPLETE")
    if not payload_tree_matches(source,entries,'src'):
        raise InstallError('SOURCE_RUNTIME_TREE_UNVERIFIED')
    return data,manifest


def project_install_paths(project):
    base=local_path(project)
    if not base.is_dir():raise InstallError('PROJECT_MUST_ALREADY_EXIST')
    output=local_path(base/'.agents/skills/codex-ads-live')
    receipt=local_path(base/'.agents/codex-ads-live-install-receipt.json')
    for folder in (base/'.agents',base/'.agents/skills'):
        if folder.exists() and not folder.is_dir():raise InstallError('PROJECT_SKILL_PARENT_NOT_DIRECTORY')
    return base,output,receipt


def plan_install(source,*,destination=None,project=None):
    if (destination is None)==(project is None):raise InstallError('INSTALL_ONE_TARGET_REQUIRED')
    source=local_path(source)
    data,manifest=verify_source(source)
    if project is not None:
        base,output,receipt=project_install_paths(project)
        new_path(output,'PROJECT_SKILL_ALREADY_EXISTS');new_path(receipt,'PROJECT_RECEIPT_ALREADY_EXISTS')
        scope='PROJECT_SKILL'
    else:
        base=local_path(destination);new_path(base,'DESTINATION_MUST_BE_NEW')
        output=base/'codex-ads-live';receipt=base/'INSTALL_RECEIPT.json';scope='PORTABLE_COPY'
    if base.is_relative_to(source):raise InstallError('DESTINATION_INSIDE_SOURCE')
    plan=dict(schema='candidate-install-plan-v1',status='INSTALL_PLAN_READY',scope=scope,package='codex-ads-live',
        version=data['version'],files=len(data['files']),payload_bytes=sum(row['bytes'] for row in data['files']),
        manifest_sha256=hashlib.sha256(manifest).hexdigest(),destination=str(output),receipt=str(receipt),
        project=str(base) if project is not None else None,production_ready=False,network_used=False,
        global_codex_config_changed=False,dependency_installed=False,host_discovery_verified=False,
        existing_files_overwritten=False,credentials_accessed=False)
    return plan,data,manifest,source,base


def _write_new(path,contents):
    local_path(path)
    with path.open('xb') as handle:
        handle.write(contents);handle.flush();os.fsync(handle.fileno())


def write_receipt(path,receipt):
    _write_new(path,(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))


def install(source,destination=None,*,project=None,preview=False):
    prepared=plan_install(source,destination=destination,project=project)
    return prepared[0] if preview else copy_install(prepared)


def copy_install(prepared):
    plan,data,manifest,source,base=prepared
    project_scope=plan['scope']=='PROJECT_SKILL'
    output=Path(plan['destination']);receipt_path=Path(plan['receipt'])
    if not project_scope:
        local_path(base);base.mkdir(parents=True,exist_ok=False)
    else:
        local_path(output.parent);output.parent.mkdir(parents=True,exist_ok=True)
    local_path(output);output.mkdir(exist_ok=False)
    # Keep a partial directory on errors for review; never clean it up silently.
    # Publish SKILL.md last, after every other payload has passed readback.
    # This reduces partial discovery; it is not an atomic multi-file install.
    for entry in sorted(data['files'],key=lambda row:(row['path']=='SKILL.md',row['path'])):
        if entry['path']=='SKILL.md':_write_new(output/'MANIFEST.json',manifest)
        target=output/entry['path'];local_path(target);target.parent.mkdir(parents=True,exist_ok=True)
        contents=read_regular_file(local_path(source/entry['path']),entry['bytes'],
                                   'SOURCE_CHANGED_DURING_COPY','SOURCE_CHANGED_DURING_COPY')
        if len(contents)!=entry['bytes'] or hashlib.sha256(contents).hexdigest()!=entry['sha256']:
            raise InstallError("SOURCE_CHANGED_DURING_COPY")
        _write_new(target,contents)
        installed=read_regular_file(target,entry['bytes'],'DESTINATION_CHECKSUM','DESTINATION_CHECKSUM')
        if len(installed)!=entry['bytes'] or hashlib.sha256(installed).hexdigest()!=entry['sha256']:
            raise InstallError('DESTINATION_CHECKSUM')
    verify_source(output)
    receipt={**plan,'status':'PROJECT_SKILL_INSTALLED_HASH_VERIFIED' if project_scope else 'COPIED_AND_HASH_VERIFIED',
        'skill_available_on_next_turn':'REQUIRES_HOST_DISCOVERY_CHECK' if project_scope else 'EXPLICIT_PATH_ONLY'}
    write_receipt(receipt_path,receipt)
    return receipt


def workspace_support(source):
    sys.path.insert(0,str(source/'src'))
    previous_bytecode_setting=sys.dont_write_bytecode
    sys.dont_write_bytecode=True
    try:
        from codex_ads_live import client_workspace as workspace
        from codex_ads_live.journal import Journal,digest
        from codex_ads_live.validation import InputError
    finally:
        sys.dont_write_bytecode=previous_bytecode_setting
        sys.path.pop(0)
    return workspace,Journal,digest,InputError


def matches_portable_restore(workspace, profile, portable):
    return (workspace.portable_config(profile)==portable and profile['binding'] is None
            and not profile.get('page_bindings')
            and all(check['status']=='ASSET_CHANGED' for check in profile['checks'].values()))


def portable_config_changes(before, after):
    collections = {}
    for key in ('assets','business_profiles','asset_rechecks'):
        old, new = before.get(key,{}), after.get(key,{})
        if old!=new:
            collections[key] = dict(added=len(new.keys()-old.keys()),removed=len(old.keys()-new.keys()),
                                    changed=sum(old[item]!=new[item] for item in old.keys()&new.keys()))
    return dict(identity_fields=[key for key in ('client_id','app_id','business_id') if before[key]!=after[key]],
                defaults=[key for key in ('ACCOUNT','PAGE') if before['defaults'][key]!=after['defaults'][key]],
                collections=collections)


def stage_project_upgrade(source, previous_project, project, *, previous_workspace=None, preview=False,
                          expected_plan_sha256=None):
    previous,target=local_path(previous_project),local_path(project)
    if previous.is_relative_to(target) or target.is_relative_to(previous):
        raise InstallError('UPGRADE_PROJECTS_MUST_BE_SEPARATE')
    prepared=plan_install(source,project=target)
    install_plan,_,_,source,_=prepared
    old_skill=local_path(previous/'.agents/skills/codex-ads-live')
    old_receipt=local_path(previous/'.agents/codex-ads-live-install-receipt.json')
    if not old_receipt.is_file() or old_receipt.stat().st_size>32000:
        raise InstallError('UPGRADE_PREVIOUS_INSTALL_RECEIPT_REQUIRED')
    old_data,old_bytes=verify_source(old_skill)
    old_sha=hashlib.sha256(old_bytes).hexdigest()
    registered=strict_manifest(old_receipt.read_text(encoding='utf-8'))
    if (type(registered) is not dict or registered.get('status')!='PROJECT_SKILL_INSTALLED_HASH_VERIFIED'
            or registered.get('scope')!='PROJECT_SKILL'
            or registered.get('manifest_sha256')!=old_sha
            or registered.get('version')!=old_data['version']
            or registered.get('files')!=len(old_data['files'])):
        raise InstallError('UPGRADE_PREVIOUS_INSTALL_IDENTITY_MISMATCH')

    old_workspace=local_path(previous_workspace if previous_workspace is not None else previous/'.codex-ads-live/client')
    if old_workspace.is_relative_to(target) or target.is_relative_to(old_workspace):
        raise InstallError('UPGRADE_WORKSPACES_MUST_BE_SEPARATE')
    data_dir=local_path(target/'.codex-ads-live')
    if data_dir.exists() and not data_dir.is_dir():
        raise InstallError('UPGRADE_DATA_PARENT_NOT_DIRECTORY')
    new_workspace=local_path(data_dir/'client')
    upgrade_log=local_path(data_dir/'upgrade')
    new_path(new_workspace,'UPGRADE_WORKSPACE_ALREADY_EXISTS')
    new_path(upgrade_log,'UPGRADE_EVIDENCE_ALREADY_EXISTS')
    old_lock=local_path(old_workspace/'operator.lock')
    if not old_lock.is_file():
        raise InstallError('UPGRADE_PREVIOUS_WORKSPACE_LOCK_REQUIRED')

    workspace,Journal,digest,InputError=workspace_support(source)
    try:
        journal=Journal(old_workspace,mode='READ_ONLY',existing_only=True)
        with journal.locked(wait_seconds=.5,existing_only=True):
            old_profile=workspace.load(journal,allow_legacy=True)
            portable=workspace.portable_config(old_profile)
            plan=dict(schema='project-skill-upgrade-v1',previous_project=str(previous),new_project=str(target),
                      previous_workspace=str(old_workspace),new_workspace=str(new_workspace),
                      previous_manifest_sha256=old_sha,new_manifest_sha256=install_plan['manifest_sha256'],
                      previous_version=old_data['version'],new_version=install_plan['version'],
                      previous_workspace_schema=old_profile['schema'],portable_config_sha256=portable['sha256'],
                      staged_receipt=str(upgrade_log/'RECEIPT.json'))
            plan_sha=digest(plan)
            if preview:
                return dict(plan,status='PROJECT_UPGRADE_PREVIEW_READY',plan_sha256=plan_sha,
                            changed_files=0,credentials_accessed=False,host_discovery_verified=False,network_used=False)
            if expected_plan_sha256 != plan_sha:
                raise InstallError('UPGRADE_PLAN_CHANGED_REVIEW_REQUIRED')
            local_path(upgrade_log);upgrade_log.mkdir(parents=True,exist_ok=False,mode=0o700)
            write_receipt(upgrade_log/'PLAN.json',dict(plan,status='PROJECT_UPGRADE_PLAN_MATCHED',plan_sha256=plan_sha))
            workspace.export_config(old_profile,upgrade_log/'backup')
            workspace.restore(upgrade_log/'backup/config.json',new_workspace)
            staged=workspace.journal_for(new_workspace,existing_only=True)
            with staged.locked(existing_only=True):
                snapshots=staged.snapshots()
                if len(snapshots)!=1:
                    raise InstallError('UPGRADE_WORKSPACE_READBACK_MISMATCH')
                restored, snapshot_sha=staged.read_snapshot(snapshots[0],workspace.validate_profile)
                if not matches_portable_restore(workspace,restored,portable):
                    raise InstallError('UPGRADE_WORKSPACE_READBACK_MISMATCH')
                restored_snapshot=dict(snapshot=snapshots[0].name,payload_sha256=snapshot_sha)
            # Make the new Skill discoverable only after the portable config readback.
            installed=copy_install(prepared)
            result=dict(plan,status='PROJECT_UPGRADE_STAGED',plan_sha256=plan_sha,
                        installed_receipt=installed['receipt'],backup_file=str(upgrade_log/'backup/config.json'),
                        restored_workspace_schema=restored['schema'],restored_workspace_snapshot=restored_snapshot,
                        original_files_overwritten=False,
                        credentials_accessed=False,authorizations_carried_forward=False,
                        runtime_state_copied=False,host_discovery_verified=False,network_used=False,
                        switch_performed=False)
            write_receipt(upgrade_log/'RECEIPT.json',result)
            return result
    except InputError as exc:
        raise InstallError(str(exc)) from None


def recorded_json(path,limit):
    with local_path(path).open('rb') as handle:
        raw=handle.read(limit+1)
    if len(raw)>limit:
        raise InstallError('UPGRADE_EVIDENCE_TOO_LARGE')
    result=strict_manifest(raw.decode('utf-8'))
    if type(result) is not dict:
        raise InstallError('UPGRADE_EVIDENCE_INVALID')
    return result


def recorded_paths_match(record, expected):
    # Compare to the inspected paths without following paths from a receipt.
    return all(isinstance(record.get(key),str) and Path(record[key])==path
               for key,path in expected.items())


def inspect_project_install(source,project):
    """Read the named project's installation checkpoints against a reference package."""
    base,skill,receipt=project_install_paths(project)
    present=dict(skill_directory=skill.is_dir(),skill=local_path(skill/'SKILL.md').is_file(),
                 manifest=local_path(skill/'MANIFEST.json').is_file(),install_receipt=receipt.is_file())
    result=dict(schema='project-skill-install-status-v1',status='PROJECT_INSTALL_NOT_STARTED',
                project=str(base),destination=str(skill),receipt=str(receipt),files_present=present,
                host_discovery_verified=False,credentials_accessed=False,network_used=False)

    def review(reason,**observations):
        return dict(result,status='PROJECT_INSTALL_REVIEW_REQUIRED',reason=reason,**observations)

    if (skill.exists() and not present['skill_directory']) or (receipt.exists() and not present['install_receipt']):
        return review('PROJECT_INSTALL_LAYOUT_INVALID')
    try:
        if not present['skill_directory']: new_path(skill,'PROJECT_SKILL_PATH_COLLISION')
        if not present['install_receipt']: new_path(receipt,'PROJECT_INSTALL_RECEIPT_PATH_COLLISION')
    except InstallError as exc:
        return review(str(exc))
    if not present['install_receipt']:
        if not present['skill_directory']:return result
        return dict(result,status='PROJECT_INSTALL_INCOMPLETE',reason='PROJECT_INSTALL_COMPLETION_RECEIPT_MISSING')
    if not all(present.values()):return review('PROJECT_INSTALL_OUTPUT_MISSING')

    try:
        raw=read_regular_file(receipt,32000,'PROJECT_INSTALL_RECEIPT_INVALID','PROJECT_INSTALL_RECEIPT_INVALID')
        record=strict_manifest(raw.decode('utf-8'))
        if (type(record) is not dict or record.get('schema')!='candidate-install-plan-v1'
                or record.get('status')!='PROJECT_SKILL_INSTALLED_HASH_VERIFIED'
                or record.get('scope')!='PROJECT_SKILL' or record.get('package')!='codex-ads-live'):
            return review('PROJECT_INSTALL_RECEIPT_INVALID')
    except (OSError,ValueError):
        return review('PROJECT_INSTALL_RECEIPT_INVALID')
    if not recorded_paths_match(record,dict(project=base,destination=skill,receipt=receipt)):
        return review('PROJECT_INSTALL_RECEIPT_PATH_MISMATCH')
    try:
        data,installed=verify_source(skill)
    except InstallError as exc:
        if str(exc)=='SOURCE_RUNTIME_TREE_UNVERIFIED':
            return review('PROJECT_INSTALL_RUNTIME_TREE_UNVERIFIED')
        return review('PROJECT_INSTALL_PAYLOAD_UNVERIFIED')
    except (OSError,ValueError):
        return review('PROJECT_INSTALL_PAYLOAD_UNVERIFIED')
    manifest_sha=hashlib.sha256(installed).hexdigest()
    if (record.get('manifest_sha256')!=manifest_sha or record.get('version')!=data['version']
            or record.get('files')!=len(data['files'])
            or record.get('payload_bytes')!=sum(row['bytes'] for row in data['files'])):
        return review('PROJECT_INSTALL_RECEIPT_IDENTITY_MISMATCH',manifest_sha256=manifest_sha)
    try:
        _,reference=verify_source(source)
    except (OSError,ValueError):
        return review('PROJECT_INSTALL_REFERENCE_PACKAGE_UNVERIFIED',manifest_sha256=manifest_sha)
    reference_sha=hashlib.sha256(reference).hexdigest()
    if reference_sha!=manifest_sha:
        return review('PROJECT_INSTALL_REFERENCE_PACKAGE_MISMATCH',manifest_sha256=manifest_sha,
                      reference_manifest_sha256=reference_sha)
    return dict(result,status='PROJECT_INSTALL_VERIFIED',version=data['version'],manifest_sha256=manifest_sha,
                install_receipt_sha256=hashlib.sha256(raw).hexdigest())


def inspect_project_upgrade(source, project):
    """Read checkpoint files in the named project without modifying either project."""
    target=local_path(project)
    if not target.is_dir():
        raise InstallError('PROJECT_MUST_ALREADY_EXIST')
    upgrade=local_path(target/'.codex-ads-live/upgrade')
    result=dict(schema='project-skill-upgrade-status-v1',status='PROJECT_UPGRADE_NOT_STARTED',
                host_discovery_verified=False,switch_verified=False,credentials_accessed=False,network_used=False)
    if not upgrade.exists():
        return result
    if not upgrade.is_dir():
        raise InstallError('UPGRADE_EVIDENCE_NOT_DIRECTORY')
    skill=local_path(target/'.agents/skills/codex-ads-live')
    client=local_path(target/'.codex-ads-live/client')
    locations={
        'plan':upgrade/'PLAN.json',
        'backup':upgrade/'backup/config.json',
        'backup_receipt':upgrade/'backup/RECEIPT.json',
        'workspace':client/'operator-state.json',
        'skill':skill/'SKILL.md',
        'install_receipt':target/'.agents/codex-ads-live-install-receipt.json',
        'upgrade_receipt':upgrade/'RECEIPT.json',
    }
    present={key:local_path(path).is_file() for key,path in locations.items()}
    if not present['upgrade_receipt']:
        return dict(result,status='PROJECT_UPGRADE_INCOMPLETE',files_present=present,
                    reason='UPGRADE_COMPLETION_RECEIPT_MISSING')

    def review(reason,**observations):
        return dict(result,status='PROJECT_UPGRADE_REVIEW_REQUIRED',files_present=present,reason=reason,**observations)

    try:
        receipt=recorded_json(locations['upgrade_receipt'],32000)
        plan={key:receipt[key] for key in UPGRADE_PLAN_FIELDS}
        if (receipt['status']!='PROJECT_UPGRADE_STAGED' or plan['schema']!='project-skill-upgrade-v1'
                or receipt['credentials_accessed'] is not False
                or receipt['authorizations_carried_forward'] is not False):
            return review('UPGRADE_COMPLETION_RECEIPT_INVALID')
        if not recorded_paths_match(receipt,dict(new_project=target,new_workspace=client,
                staged_receipt=locations['upgrade_receipt'],backup_file=locations['backup'],
                installed_receipt=locations['install_receipt'])):
            return review('UPGRADE_TARGET_PATH_MISMATCH')
        if not all(present.values()):
            return review('UPGRADE_OUTPUT_MISSING')
        install=inspect_project_install(source,target)
        if install['status']!='PROJECT_INSTALL_VERIFIED':
            reason=install.get('reason')
            if reason=='PROJECT_INSTALL_RECEIPT_PATH_MISMATCH':
                return review('UPGRADE_INSTALL_RECEIPT_PATH_MISMATCH')
            if reason=='PROJECT_INSTALL_REFERENCE_PACKAGE_UNVERIFIED':
                return review('UPGRADE_REFERENCE_PACKAGE_UNVERIFIED',manifest_sha256=install['manifest_sha256'])
            if reason=='PROJECT_INSTALL_REFERENCE_PACKAGE_MISMATCH':
                return review('UPGRADE_REFERENCE_PACKAGE_MISMATCH',manifest_sha256=install['manifest_sha256'],
                              reference_manifest_sha256=install['reference_manifest_sha256'])
            return review('UPGRADE_INSTALLED_PACKAGE_MISMATCH',install_reason=reason)
        manifest_sha=install['manifest_sha256']
        if manifest_sha!=plan['new_manifest_sha256'] or install['version']!=plan['new_version']:
            return review('UPGRADE_INSTALLED_PACKAGE_MISMATCH')
    except (OSError,ValueError,KeyError,TypeError):
        return review('UPGRADE_LOCAL_EVIDENCE_UNREADABLE')

    workspace,Journal,digest,InputError=workspace_support(local_path(source))
    staged_evidence={}
    try:
        plan_sha=digest(plan)
        if plan_sha!=receipt['plan_sha256']:
            return review('UPGRADE_COMPLETION_RECEIPT_INVALID')
        if recorded_json(locations['plan'],32000)!=dict(plan,status='PROJECT_UPGRADE_PLAN_MATCHED',plan_sha256=plan_sha):
            return review('UPGRADE_RECORDED_PLAN_MISMATCH')
        raw=read_regular_file(local_path(locations['backup']),512000,'UPGRADE_BACKUP_INVALID','UPGRADE_EVIDENCE_TOO_LARGE')
        backup=strict_manifest(raw.decode('utf-8'))
        if digest(backup['config'])!=backup['sha256'] or backup['sha256']!=plan['portable_config_sha256']:
            return review('UPGRADE_BACKUP_DIGEST_MISMATCH')
        backup_receipt=recorded_json(locations['backup_receipt'],32000)
        file_entry=dict(path='config.json',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        if backup_receipt.get('credentials_included') is not False or backup_receipt.get('files')!=[file_entry]:
            return review('UPGRADE_BACKUP_RECEIPT_MISMATCH')
        journal=Journal(client,mode='READ_ONLY',existing_only=True)
        with journal.locked(existing_only=True):
            snapshots=journal.snapshots()
            if not snapshots or snapshots[0].name!='snapshot-0000000001.json':
                return review('UPGRADE_RESTORED_WORKSPACE_SNAPSHOT_MISMATCH')
            first=snapshots[0]
            try:
                initial, snapshot_sha=journal.read_snapshot(first,workspace.validate_profile)
            except (OSError,ValueError,TypeError,KeyError):
                return review('UPGRADE_RESTORED_WORKSPACE_SNAPSHOT_MISMATCH')
            restored_snapshot=dict(snapshot=first.name,payload_sha256=snapshot_sha)
            recorded='restored_workspace_snapshot' in receipt
            if ((recorded and receipt['restored_workspace_snapshot']!=restored_snapshot)
                    or not matches_portable_restore(workspace,initial,backup)):
                return review('UPGRADE_RESTORED_WORKSPACE_SNAPSHOT_MISMATCH')
            staged_evidence=dict(plan_sha256=plan_sha,manifest_sha256=manifest_sha,portable_config_sha256=backup['sha256'],
                restored_workspace_snapshot=restored_snapshot,
                restored_snapshot_binding='RECORDED_AT_STAGE' if recorded else 'LEGACY_INITIAL_SNAPSHOT_CHECKED')
            current=workspace.load(journal)
            current_portable=workspace.portable_config(current)
            current_matches=current_portable==backup
            current_evidence=dict(current_workspace_binding_recorded=bool(current['binding'] or current.get('page_bindings')),
                                  current_workspace_matches_stage=current_matches)
            if not current_matches:
                return review('UPGRADE_WORKSPACE_CHANGED_SINCE_STAGE',**staged_evidence,**current_evidence,
                              current_workspace_changes=portable_config_changes(backup['config'],current_portable['config']))
    except InputError as exc:
        return review(str(exc),**staged_evidence)
    except (OSError,ValueError,KeyError,TypeError):
        return review('UPGRADE_LOCAL_EVIDENCE_UNREADABLE',**staged_evidence)
    return dict(result,status='PROJECT_UPGRADE_STAGED_VERIFIED',files_present=present,**staged_evidence,**current_evidence)


def main(argv=None):
    source = Path(__file__).resolve().parent
    parser = InstallArgumentParser(prog='install.py', description="预览/复制候选或安装到既有项目的.agents/skills；不改全局配置")
    target=parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--destination",type=Path)
    target.add_argument("--project",type=Path)
    parser.add_argument('--preview',action='store_true')
    parser.add_argument('--upgrade-from',type=Path,help='原项目。将新版本和非秘密档案分阶段接续到独立项目')
    parser.add_argument('--previous-workspace',type=Path,help='原项目使用自定义工作区路径时填写')
    parser.add_argument('--upgrade-plan-sha256',help='执行分阶段升级时提供刚预览的方案摘要')
    status=parser.add_mutually_exclusive_group()
    status.add_argument('--upgrade-status',action='store_true',help='只读核对新版项目的升级步骤与完成回执')
    status.add_argument('--install-status',action='store_true',help='只读核对已安装项目的路径、包清单与完成回执')
    args=parser.parse_args(argv)
    try:
        if args.install_status or args.upgrade_status:
            if (args.project is None or args.preview or args.upgrade_from is not None
                    or args.previous_workspace is not None or args.upgrade_plan_sha256 is not None):
                raise InstallError('INSTALL_STATUS_REQUIRES_PROJECT_ONLY' if args.install_status
                                   else 'UPGRADE_STATUS_REQUIRES_PROJECT_ONLY')
            inspect=inspect_project_install if args.install_status else inspect_project_upgrade
            receipt=inspect(source,args.project)
        elif args.upgrade_from is not None:
            if args.project is None:
                raise InstallError('UPGRADE_REQUIRES_PROJECT_TARGET')
            if not args.preview and not re.fullmatch('[0-9a-f]{64}',args.upgrade_plan_sha256 or ''):
                raise InstallError('UPGRADE_PREVIEW_HASH_REQUIRED')
            receipt=stage_project_upgrade(source,args.upgrade_from,args.project,previous_workspace=args.previous_workspace,
                                          preview=args.preview,expected_plan_sha256=args.upgrade_plan_sha256)
        else:
            if args.previous_workspace is not None or args.upgrade_plan_sha256 is not None:
                raise InstallError('UPGRADE_PREVIOUS_PROJECT_REQUIRED')
            receipt=install(source,args.destination,project=args.project,preview=args.preview)
    except InstallError as exc:
        print(json.dumps({"status":"REJECTED","error_code":str(exc)}));return 2
    except (OSError,ValueError,TypeError,KeyError):
        print('{"status":"REJECTED","error_code":"INSTALL_LOCAL_FAILURE"}');return 2
    unicode_terminal = sys.stdout.isatty() and sys.stdout.encoding == 'utf-8'
    print(json.dumps(receipt,ensure_ascii=not unicode_terminal,indent=2));return 0


if __name__=="__main__":sys.exit(main())
