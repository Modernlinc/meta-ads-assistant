"""Non-secret local preflight and blank preparation form; never authenticates."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import os
from pathlib import Path
import platform
import re
import sys
from zoneinfo import ZoneInfoNotFoundError

from . import __version__
from .journal import canonical
from .local_paths import checked_path
from .timezones import BUNDLE_RELEASE, resolve_zone
from .validation import fail


def reported_os_build(system, release, version):
    """Summarize a build without copying arbitrary platform text into a handoff."""
    build = None
    if all(type(value) is str and 1 <= len(value) <= 512 and value.isprintable() for value in (release,version)):
        build = hashlib.sha256(canonical([release,version]).encode('utf-8')).hexdigest()
    release_number = release if type(release) is str and re.fullmatch(r'[0-9]{1,8}(?:\.[0-9]{1,8}){0,4}',release) else None
    windows_version = (version if system=='Windows' and type(version) is str
                       and re.fullmatch(r'[0-9]{1,8}(?:\.[0-9]{1,8}){2,4}',version) else None)
    return dict(reported_system_release=release_number,reported_windows_version=windows_version,
                recorded_os_build_sha256=build)


def preflight():
    package = Path(__file__).resolve().parents[2]
    checks = {}
    checks['python'] = 'PASS' if sys.version_info >= (3, 12) else 'UPGRADE_REQUIRED'
    system = platform.system()
    checks['operating_system'] = 'IN_SCOPE' if system in ('Darwin', 'Windows') else 'OUTSIDE_V1_SUPPORTED_OS'
    zones = {}
    zone_sources = {}
    for name in ('UTC', 'America/Los_Angeles', 'Asia/Shanghai'):
        try:
            _, zone_sources[name] = resolve_zone(name)
            zones[name] = 'AVAILABLE'
        except ZoneInfoNotFoundError:
            zones[name] = 'MISSING'
            zone_sources[name] = None
    checks['timezone_data'] = 'PASS' if all(v == 'AVAILABLE' for v in zones.values()) else 'DEPENDENCY_REVIEW_REQUIRED'
    checks['package_integrity'] = 'NOT_CHECKED'; count = None; manifest_hash = None
    # Reuse the exact installer checks, without installing or creating files.
    try:
        spec = importlib.util.spec_from_file_location('codex_ads_live_installer', package / 'install.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        data, manifest = module.verify_source(package)
        count = len(data['files']); manifest_hash = hashlib.sha256(manifest).hexdigest()
        checks['package_integrity'] = 'PASS'
    except (OSError, ValueError, TypeError, KeyError, AttributeError): checks['package_integrity'] = 'FAILED'
    ready = all(checks[key] == value for key, value in (
        ('python', 'PASS'), ('operating_system', 'IN_SCOPE'), ('timezone_data', 'PASS'), ('package_integrity', 'PASS')))
    release = platform.release()
    build = reported_os_build(system,release,platform.version())
    return {
        'schema': 'local-preflight-v1', 'package_version': __version__,
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'status': 'LOCAL_PREREQUISITES_PRESENT' if ready else 'LOCAL_ACTION_REQUIRED',
        'system': system, 'system_release': release, 'architecture': platform.machine(),
        'os_build_sha256': build['recorded_os_build_sha256'],
        'python': platform.python_version(), 'checks': checks, 'timezone_data': zones,
        'timezone_sources': zone_sources, 'bundled_iana_release': BUNDLE_RELEASE,
        'package_file_count': count, 'manifest_sha256': manifest_hash,
        'checks_are_native_acceptance': False, 'customer_acceptance': 'NOT_RUN',
        'real_connection_verified': False, 'credential_storage_accessed': False,
        'network_used': False, 'environment_variables_collected': False,
        'global_config_changed': False, 'dependencies_installed': False,
        'writable_destination': 'NOT_TESTED_USE_PREPARE_NEW_DIRECTORY',
    }


PENDING_REVIEW_STATES = ('UNKNOWN','NOT_REQUIRED','NOT_STARTED','DRAFT','SUBMITTED','NEEDS_INFORMATION','REJECTED')
PLATFORM_PERMISSION_ITEMS = ('ads_read','pages_read_engagement','read_insights')
PLATFORM_ACCESS_LEVELS = ('UNKNOWN','STANDARD_ACCESS','ADVANCED_ACCESS')
PLATFORM_REVIEW_STATES = {
    'app_publication': ('UNKNOWN','NOT_PUBLISHED','PUBLISHED','NO_PUBLICATION_STEP_FOR_APP_TYPE'),
    'business_verification': (*PENDING_REVIEW_STATES,'VERIFIED'),
    **dict.fromkeys(PLATFORM_PERMISSION_ITEMS,(*PENDING_REVIEW_STATES,'APPROVED')),
}


def blank_preparation():
    platform_items = {key:dict(state='UNKNOWN',evidence_ref=None) for key in PLATFORM_REVIEW_STATES}
    for key in PLATFORM_PERMISSION_ITEMS:
        platform_items[key]['current_access_level'] = 'UNKNOWN'
    return {
        'schema': 'blank-customer-preparation-v1', 'mode': 'MOCK',
        'assets': {kind: {'id': None, 'ownership_evidence_ref': None}
                   for kind in ('business_portfolio', 'app', 'ad_account', 'facebook_page')},
        'page_identity_type': 'FACEBOOK_PAGE',
        'account_context': {'currency': None, 'timezone': None},
        'requested_work': {'ads_read': True, 'page_live_read': False},
        'existing_live_id': None,
        'review': {'scope_approved': False, 'identity_and_asset_binding_verified': False,
                   'actual_grants_verified': False, 'reviewed_at': None, 'expires_at': None},
        'platform_review': dict(app_id=None,business_portfolio_id=None,observed_at=None,
                                items=platform_items),
        'real_connection_enabled': False, 'real_write_enabled': False,
        'authority': 'PREPARATION_ONLY_NOT_AUTHORIZATION',
    }


def prepare(directory):
    root = checked_path(directory, link_error='PREPARE_PATH_SYMLINK', volume_error='PREPARE_VOLUME_NOT_MOUNTED')
    if root.exists() or root.is_symlink(): fail('PREPARE_REQUIRES_NEW_DIRECTORY')
    report = preflight()
    if report['checks']['package_integrity'] != 'PASS': fail('PREPARE_PACKAGE_INTEGRITY_REQUIRED')
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    items = []
    documents = {
        'preflight.json': canonical(report) + '\n',
        'preparation.json': canonical(blank_preparation()) + '\n',
        'READ_FIRST.md': '''# 客户准备目录

这是空白准备表，不是连接配置或授权。先读工具包 references/CUSTOMER_SOP.md。
preflight.json 记录本机版本、时区数据和包完整性；通过不代表Windows/macOS完整验收或Meta连接。
preparation.json 的编号、币种、时区和证据均为空。经本人明确授权后，才按SOP核对具名资产；不要在这里填写Token、密码、Cookie、App Secret、证照、人员或评论。
review字段只是待办；模型或使用者改成true不会启用真实操作，CLI不消费此文件作为认证依据。
platform_review只记本人观察的App发布、公司验证、所选权限的审核状态与当前访问级别。按客户SOP第4步填同一App及观察时间，原始截图、说明和证照由本人保管。
可用run.py preparation-review preparation.json检查当前阶段与缺项；--output指定全新目录保存报告。资产id用数字字符串，ownership_evidence_ref只填EV_大写字母/数字/下划线别名，不填路径或URL；原证据由本人保管，此命令不打开引用。不确定字段保持null/false。
凭据授权需本人在官方页面/系统安全存储完成，本命令不提供凭据录入。
不要把已填真实资产的准备目录放入公共课程或安装包。两台Mac接续先停任务，保留旧状态，不共享活动目录。
失败时保留现有文件检查；本命令不覆盖、不删除、不安装依赖、不改全局配置。
''',
    }
    for name, content in documents.items():
        data = content.encode('utf-8')
        with (root / name).open('xb') as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        items.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    receipt = dict(schema='blank-preparation-receipt-v1', status='BLANK_PREPARATION_CREATED',
                   artifacts=items, actual_customer_data_written=False,
                   credential_storage_accessed=False, real_connection_enabled=False, real_write_enabled=False)
    with (root / 'RECEIPT.json').open('x', encoding='utf-8') as handle:
        handle.write(canonical(receipt) + '\n'); handle.flush(); os.fsync(handle.fileno())
    return receipt
