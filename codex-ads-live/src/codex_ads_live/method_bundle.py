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
                  source_directory=str(root/'methods'/skill), read_files=[row['path'] for row in entries],
                  method_sha256=next(row['sha256'] for row in entries if row['path']=='method.md'),
                  next_step='先读 methods/ADAPTATION.md，再按任务读取方法及引用；脚本未捆绑，不执行上游安装/历史操作指令')
    return result


# The four extracted methods remain available in the main installation.
# Independent partner products retain the original explicit source-root contract.
PARTNER_SKILLS = {
    'product-selection': 'PRODUCT_SELECTION', 'shopify-store': 'STOREFRONT',
    'ecommerce-backoffice': 'BACKOFFICE', 'codex-ecommerce': 'WORKSPACE_OPERATIONS',
    'google-ads': 'SEARCH_ADS', 'tiktok-content': 'SHORT_VIDEO', 'social-content': 'SOCIAL_CONTENT',
    'influencer-collaboration': 'CREATOR_PARTNERS', 'amazon-us': 'MARKETPLACE', 'google-seo': 'SEARCH_CONTENT',
    'reddit-research': 'COMMUNITY_RESEARCH', 'pinterest-commerce': 'PRODUCT_CATALOG',
    'customer-support': 'CUSTOMER_SUPPORT', 'b2b-quotation': 'SALES_INQUIRY',
}
CORE_METHOD_ROLES = {
    'meta-facebook-ads': 'META_AD_PLANNING',
    'ad-creative': 'CREATIVE_METHOD',
    'ecommerce-analytics': 'COMMERCE_ANALYTICS',
    'brand-brief': 'BRAND_POSITIONING',
}
def _research_lock():
    path = Path(__file__).resolve().parents[2] / 'references' / 'external-research-methods-lock.json'
    if not path.is_file() or path.stat().st_size > 100_000:
        return dict(repositories={}, methods={})
    lock = json.loads(path.read_bytes())
    if lock['schema'] != 'external-research-method-lock-v1':
        fail('METHOD_SOURCE_IDENTITY')
    return lock


RESEARCH_LOCK = _research_lock()
METHOD_ROLES = dict(CORE_METHOD_ROLES, **PARTNER_SKILLS)
METHOD_ROLES.update({name: value[2] for name, value in RESEARCH_LOCK['methods'].items()})
NAMED_METHODS = tuple(sorted(METHOD_ROLES))


def source_commit_for(skill):
    info = RESEARCH_LOCK['methods'].get(skill)
    return COMMIT if info is None else RESEARCH_LOCK['repositories'][info[0]][1]


def external_study(skill, source_root):
    from .local_paths import checked_path

    group, entry, role, dependencies = RESEARCH_LOCK['methods'][skill]
    repo, commit = RESEARCH_LOCK['repositories'][group]
    result = dict(handler=skill, source_repository=repo, source_commit=commit, status='SOURCE_DIRECTORY_REQUIRED',
                  source_download=f'https://github.com/{repo}/archive/{commit}.zip',
                  next_entry='workspace-method-record', method_execution='NOT_RUN_BY_ROUTER',
                  scripts_dispatched=False, live_action_authorized=False, usage='DOCUMENTS_ONLY_READ_NO_UPSTREAM_TOOLS')
    if source_root is None:
        return result
    base = checked_path(source_root, link_error='METHOD_SYMLINK', volume_error='METHOD_VOLUME_REQUIRED')
    root = checked_path(base / Path(entry).parent, link_error='METHOD_SYMLINK', volume_error='METHOD_VOLUME_REQUIRED')
    for name, expected in dependencies.items():
        path = _safe_file(root, Path(name))
        if not path.is_file():
            return dict(result, status='SOURCE_DEPENDENCY_MISSING', dependency=name)
        with path.open('rb') as stream:
            raw = stream.read(600_001)
        if len(raw) > 600_000 or hashlib.sha256(raw).hexdigest() != expected:
            return dict(result, status='SOURCE_VERSION_MISMATCH', dependency=name)
    return dict(result, status='SOURCE_METHOD_VERIFIED', partner_role=role, source_directory=str(root),
                method='SKILL.md', method_sha256=dependencies['SKILL.md'], files_verified=len(dependencies),
                read_files=list(dependencies))


def external_route(skill, source_root=None, *, workspace=None):
    from .local_paths import checked_path

    if skill not in NAMED_METHODS:
        fail('OUTSIDE_V1_SCOPE')
    if skill in RESEARCH_LOCK['methods']:
        if source_root is None and workspace is not None:
            from .method_library import lookup

            installed=lookup(workspace,skill)
            if installed is not None:
                return installed
        status=external_study(skill,source_root)
        if status['status']=='SOURCE_DIRECTORY_REQUIRED':
            status.update(install_command=['method-install','--workspace','WORKSPACE','--skill',skill])
        return status
    result = dict(handler=skill, source_commit=COMMIT, status='SOURCE_DIRECTORY_REQUIRED',
                  method_execution='NOT_RUN_BY_ROUTER', live_action_authorized=False,
                  source_download=f'https://github.com/Modernlinc/ecommerce-skills/archive/{COMMIT}.zip',
                  next_entry='workspace-method-record' if skill in PARTNER_SKILLS else {
                      'meta-facebook-ads': 'media-buyer prepare',
                      'ad-creative': 'workspace-creative-research',
                      'ecommerce-analytics': 'commerce-reconcile',
                      'brand-brief': 'workspace-creative-research',
                  }[skill])
    if source_root is None:
        bundled_intent = next((kind for kind, method in METHODS.items() if skill == method), None)
        if bundled_intent:
            bundle = bundled_route(bundled_intent)
            bundle.update(source_download=result["source_download"], next_entry=result["next_entry"])
            return bundle
        return result
    base = checked_path(source_root, link_error='METHOD_SYMLINK', volume_error='METHOD_VOLUME_REQUIRED')
    root = checked_path(base / 'skills' / skill, link_error='METHOD_SYMLINK', volume_error='METHOD_VOLUME_REQUIRED')
    lock_path = Path(__file__).resolve().parents[2] / 'references' / 'external-methods-lock.json'
    if lock_path.stat().st_size > 200_000:
        fail('METHOD_LOCK_TOO_LARGE')
    lock = json.loads(lock_path.read_bytes())
    if (lock['schema'], lock['repository'], lock['commit']) != (
            'external-method-lock-v1', 'Modernlinc/ecommerce-skills', COMMIT):
        fail('METHOD_SOURCE_IDENTITY')
    scope = lock['methods'][skill]
    for relative, proof in scope['files'].items():
        path = _safe_file(root, Path(relative))
        if not path.is_file():
            return dict(result, status='SOURCE_DEPENDENCY_MISSING', dependency=relative)
        if path.stat().st_size != proof['bytes']:
            return dict(result, status='SOURCE_VERSION_MISMATCH', dependency=relative)
        with path.open('rb') as src:
            data = src.read(proof['bytes'] + 1)
        if hashlib.sha256(data).hexdigest() != proof['sha256']:
            return dict(result, status='SOURCE_VERSION_MISMATCH', dependency=relative)
    return dict(result, status='SOURCE_METHOD_VERIFIED', source_directory=str(root), entry=scope['entry'],
                method=scope['method'], files_verified=len(scope['files']),
                method_sha256=scope['files'][scope['method']]['sha256'],
                partner_role=METHOD_ROLES[skill], scripts_dispatched=False)
