"""Verify every selected method dependency, not only its entry-point name."""
import hashlib
import json
from pathlib import Path
import re
from .validation import fail

METHODS={'ads_plan':'meta-facebook-ads','creative':'ad-creative',
         'analytics':'ecommerce-analytics','positioning':'brand-brief'}
COMMIT='1fc556b3a8b900180b63af9ec8966bcc52489dd9'


def _safe_file(root,relative):
    path=root/relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):fail('METHOD_PATH_OUTSIDE_BUNDLE')
    for parent in path.parents:
        if parent==root:break
        if parent.is_symlink():fail('METHOD_SYMLINK')
    return path


def bundled_route(intent,package_root=None):
    if type(intent) is not str or intent not in METHODS:fail('OUTSIDE_V1_SCOPE')
    root=Path(package_root) if package_root is not None else Path(__file__).resolve().parents[2]
    skill=METHODS[intent]
    result={'handler':skill,'source_commit':COMMIT,'status':'SOURCE_NOT_AVAILABLE',
            'live_action_authorized':False,'causal_claim_allowed':False,'scripts_available':False,
            'method_execution':'NOT_RUN_BY_ROUTER','history_inherited':False}
    try:
        lock_path=_safe_file(root,Path('references/method-bundles-lock.json'))
        if not lock_path.is_file():return result
        if lock_path.stat().st_size>200_000:fail('METHOD_LOCK_TOO_LARGE')
        lock=json.loads(lock_path.read_text(encoding='utf-8'))
        if lock['repository']!='Modernlinc/ecommerce-skills' or lock['commit']!=COMMIT:fail('METHOD_SOURCE_IDENTITY')
        selected=next(x for x in lock['bundles'] if x['skill']==skill)
        if selected['entry']!=f'methods/{skill}/entry.md' or selected['method']!=f'methods/{skill}/method.md':fail('METHOD_ENTRY_PATH')
        expected_manifest=Path('methods')/skill/'BUNDLE_MANIFEST.json'
        if selected['manifest']!=expected_manifest.as_posix():fail('METHOD_MANIFEST_PATH')
        manifest_path=_safe_file(root,expected_manifest)
        if not manifest_path.is_file():return result
        if manifest_path.stat().st_size>200_000:fail('METHOD_MANIFEST_TOO_LARGE')
        data=manifest_path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=selected['manifest_sha256']:
            result['status']='SOURCE_VERSION_MISMATCH';return result
        manifest=json.loads(data)
        if manifest['skill']!=skill or manifest['commit']!=COMMIT:fail('METHOD_SOURCE_IDENTITY')
        entries=manifest['files']
        if type(entries) is not list or not 3<=len(entries)<=100:fail('METHOD_FILES')
        names=set()
        for entry in entries:
            name=entry['path']
            if type(name) is not str or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,79}',name) or name in names:fail('METHOD_FILE_PATH')
            names.add(name);path=_safe_file(root,Path('methods')/skill/name)
            if not path.is_file():return result
            if path.stat().st_size>200_000:fail('METHOD_FILE_TOO_LARGE')
            contents=path.read_bytes()
            if len(contents)!=entry['bytes'] or hashlib.sha256(contents).hexdigest()!=entry['sha256']:
                result['status']='SOURCE_VERSION_MISMATCH';return result
        if not {'entry.md','method.md','LICENSE.txt','NOTICE.md'}<=names:fail('METHOD_REQUIRED_DEPENDENCIES')
    except (OSError,ValueError,KeyError,TypeError,StopIteration):
        # No raw filesystem path or upstream response in the public error.
        return {**result,'status':'SOURCE_INVALID_OR_UNREADABLE'}
    result.update(status='BUNDLED_PRINCIPLES_ONLY_ENGINE_UNAVAILABLE' if intent=='analytics' else 'BUNDLED_TEXT_METHOD_HASH_VERIFIED',
                  files_verified=len(entries),entry=selected['entry'],method=selected['method'],
                  next_step='先读 methods/ADAPTATION.md，再按任务读取方法及引用；脚本未捆绑，不执行上游安装/历史操作指令')
    return result
