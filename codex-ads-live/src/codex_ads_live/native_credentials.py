"""Opt-in reference reader for pre-existing OS-owned generic credentials.

Does not create, enumerate, export, refresh, delete or copy a credential store.
Only separately authorized interactive connection checks or foreground read
runs can enable it.
Any native test evidence is scoped to its
specific fixture; it does not verify a newly supplied account connection.
"""
from contextlib import contextmanager
from dataclasses import asdict
import re
import subprocess
import sys

from .http_read import CredentialBroker, ReadBinding
from .journal import digest
from .read_adapter import ReadPlan
from .validation import InputError, fail


def _mac_read(service, account):
    try:
        completed = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', service, '-a', account, '-w'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                   timeout=15, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        raise InputError('NATIVE_CREDENTIAL_UNAVAILABLE') from None
    if completed.returncode != 0: fail('NATIVE_CREDENTIAL_UNAVAILABLE')
    if len(completed.stdout) > 8194: fail('NATIVE_CREDENTIAL_SIZE')
    try: return completed.stdout.rstrip(b'\r\n').decode('ascii')
    except UnicodeError: raise InputError('NATIVE_CREDENTIAL_ENCODING') from None


def _windows_read(target, account):
    # Windows-only FFI, never imported/invoked by macOS fake-OS tests.
    import ctypes
    from ctypes import wintypes
    class Credential(ctypes.Structure):
        _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
                    ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
                    ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
                    ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
                    ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                    ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]
    library = ctypes.WinDLL('advapi32', use_last_error=True)
    pointer = ctypes.POINTER(Credential)()
    library.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
    library.CredReadW.restype = wintypes.BOOL
    library.CredFree.argtypes = [ctypes.c_void_p]; library.CredFree.restype = None
    if not library.CredReadW(target, 1, 0, ctypes.byref(pointer)): fail('NATIVE_CREDENTIAL_UNAVAILABLE')
    try:
        item = pointer.contents
        if item.Type != 1 or item.TargetName != target or item.UserName != account:
            fail('NATIVE_CREDENTIAL_IDENTITY_MISMATCH')
        size = item.CredentialBlobSize
        if not 2 <= size <= 2560 or size % 2 or not item.CredentialBlob: fail('NATIVE_CREDENTIAL_SIZE')
        try: return ctypes.string_at(item.CredentialBlob, size).decode('utf-16-le')
        except UnicodeError: raise InputError('NATIVE_CREDENTIAL_ENCODING') from None
    finally: library.CredFree(pointer)


def credential_labels(reference, app_id, principal_id, page_id=None):
    if type(reference) is not str or not re.fullmatch(r'conn_[0-9a-f]{32}', reference): fail('CREDENTIAL_REFERENCE_INVALID')
    identifiers = (app_id,principal_id) if page_id is None else (app_id,principal_id,page_id)
    for identifier in identifiers:
        if type(identifier) is not str or not re.fullmatch(r'[0-9]{1,40}', identifier): fail('CONNECTION_ID_REQUIRED')
    service = 'ModernLinc.CodexAdsLive.' + reference
    account = app_id + '.' + principal_id
    if page_id is not None:
        service += '.Page.' + page_id
        account += '.' + page_id
    return service, account


class NativeCredentialBroker(CredentialBroker):
    def __init__(self, reference, binding, enabled=False, *, page_plan=None):
        if type(binding) is not ReadBinding or type(enabled) is not bool: fail('CREDENTIAL_BINDING_REQUIRED')
        page_id = None
        if page_plan is not None:
            if (type(page_plan) is not ReadPlan or page_plan.operation!='page'
                    or binding.plan_hash!=digest(asdict(page_plan))):
                fail('CREDENTIAL_BINDING_CHANGED')
            page_id = page_plan.page_id
        self.reference = reference; self.expected_binding_hash = digest(asdict(binding)); self.enabled = enabled
        self.service, self.account = credential_labels(reference,binding.app_id,binding.principal_id,page_id)

    def metadata(self):
        return {'reference': self.reference, 'enabled': self.enabled, 'storage': 'OS_PREEXISTING_GENERIC_ONLY',
                'native_acceptance': 'NOT_RUN', 'native_acceptance_scope': 'THIS_CONNECTION',
                'oauth': 'NOT_IMPLEMENTED', 'auto_refresh': False}

    @contextmanager
    def lease(self, binding):
        if not self.enabled: fail('NATIVE_CREDENTIAL_ACCESS_NOT_ENABLED')
        if type(binding) is not ReadBinding or digest(asdict(binding)) != self.expected_binding_hash:
            fail('CREDENTIAL_BINDING_CHANGED')
        try:
            if sys.platform == 'darwin': credential = _mac_read(self.service, self.account)
            elif sys.platform == 'win32': credential = _windows_read(self.service, self.account)
            else: fail('NATIVE_CREDENTIAL_PLATFORM_UNSUPPORTED')
        except InputError: raise
        except Exception: raise InputError('NATIVE_CREDENTIAL_UNAVAILABLE') from None
        if type(credential) is not str or not re.fullmatch(r'[A-Za-z0-9._~+/=-]{1,8192}', credential):
            fail('CREDENTIAL_FORMAT_INVALID')
        try: yield credential
        finally: credential = None  # Python memory cannot be guaranteed zeroized.
