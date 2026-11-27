"""Install and locate pinned text-only methods in one client workspace."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
from pathlib import Path, PurePosixPath
import re
from time import monotonic
from urllib.parse import quote
from uuid import uuid4

from .client_workspace import read
from .http_read import arm_http_deadline
from .journal import Journal, digest
from .local_paths import checked_path
from .method_bundle import RESEARCH_LOCK, external_study
from .validation import InputError, fail

_MAX_FILE = 600_000
_MAX_BUNDLE = 4_000_000
_SHA = re.compile(r'[a-f0-9]{64}')
_SLUG = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*')
_BASE = Path(__file__).resolve().parents[2]


def _clean_path(path):
    parts = path.split('/')
    if not parts or any(not _SLUG.fullmatch(part) or part.endswith('.') for part in parts):
        fail('METHOD_INSTALL_INVALID_LOCK')
    return path


def _spec(skill):
    selection = RESEARCH_LOCK['methods'].get(skill)
    if selection is None:
        fail('METHOD_INSTALL_OPTIONAL_ONLY')
    group, entry, _, files = selection
    repository, commit = RESEARCH_LOCK['repositories'][group]
    if not re.fullmatch('[a-f0-9]{40}',commit) or not re.fullmatch('[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+',repository):
        fail('METHOD_INSTALL_INVALID_LOCK')
    _clean_path(repository)
    provenance = json.loads((_BASE/'references/external-method-provenance.json').read_bytes())
    legal = provenance['sources'][group]
    if (provenance['schema']!= 'external-method-provenance-v1'
            or (legal['repository'],legal['commit'],legal['license'],legal['license_file'])
            != (repository,commit,'MIT','LICENSE')
            or not _SHA.fullmatch(legal['license_sha256'])):
        fail('METHOD_INSTALL_LICENSE_UNVERIFIED')
    if not 1<=len(files)<=50 or 'SKILL.md' not in files or PurePosixPath(entry).name!='SKILL.md':
        fail('METHOD_INSTALL_INVALID_LOCK')
    parent=PurePosixPath(_clean_path(entry)).parent
    paths = {'LICENSE':legal['license_sha256']}
    for relative, sha in files.items():
        if not relative.endswith('.md') or not _SHA.fullmatch(sha):
            fail('METHOD_INSTALL_INVALID_LOCK')
        paths[f'{parent}/{_clean_path(relative)}'] = sha
    notice = (f'# Method attribution\n\n'
              f'Method: {skill}\n\n'
              f'Source: https://github.com/{repository}/tree/{commit}\n\n'
              'License: MIT. See the LICENSE at this bundle root. Only pinned method text is installed.\n')
    return repository,commit,paths,notice


def _fetch(repository, commit, path, deadline):
    resource=quote(path,safe='/')
    raw_endpoint=f'/{repository}/{commit}/{resource}'
    endpoints=(
        ('api.github.com',f'/repos/{repository}/contents/{resource}?ref={commit}','application/vnd.github.raw+json'),
        ('raw.githubusercontent.com',f'{raw_endpoint}?plain=1','text/plain'),
        ('raw.githubusercontent.com',f'{raw_endpoint}?v={commit}','text/plain'),
        ('raw.githubusercontent.com',raw_endpoint,'text/plain'),
    )
    misses = 0
    for host, endpoint, accept in endpoints:
        status, data = _read_public_file(host,endpoint,accept,deadline)
        if data is not None:
            return data
        misses += status in (404, 410)
    if misses == len(endpoints):
        fail('METHOD_PINNED_SOURCE_NOT_FOUND')
    fail('METHOD_DOWNLOAD_RETRY_OR_OFFLINE')


def _read_public_file(host, endpoint, accept, deadline):
    remaining=deadline-monotonic()
    if remaining<=0:
        fail('METHOD_DOWNLOAD_RETRY_OR_OFFLINE')
    timeout=min(8,remaining)
    connection=http.client.HTTPSConnection(host,timeout=timeout)
    try:
        arm_http_deadline(connection,timeout)
        connection.request('GET',endpoint,headers={'Accept':accept,'User-Agent':'codex-ads-live-methods'})
        response=connection.getresponse()
        if response.status != 200:
            return response.status, None
        data=response.read(_MAX_FILE+1)
        if len(data)>_MAX_FILE:
            fail('METHOD_DOWNLOAD_SIZE_LIMIT')
        return response.status, data
    except (OSError,http.client.HTTPException):
        return None, None
    finally:
        connection.close()


def _library(directory, *, create=False):
    read(directory, existing_only=True)
    path=checked_path(Path(directory)/'method-library',link_error='METHOD_SYMLINK',volume_error='METHOD_VOLUME_REQUIRED')
    if not create and not path.exists():
        return None
    return Journal(path,stage_snapshots=True,existing_only=not create)


def _validate_index(value):
    if type(value) is not dict or len(value)>500:
        fail('METHOD_LIBRARY_INDEX')
    for name,row in value.items():
        if (not _SLUG.fullmatch(name) or type(row) is not dict or set(row)!={'revision','spec_sha256','active'}
                or type(row['active']) is not bool or not re.fullmatch('[a-f0-9]{32}',row['revision'])
                or not _SHA.fullmatch(row['spec_sha256'])):
            fail('METHOD_LIBRARY_INDEX')


def _read_index(journal):
    saved, damaged=journal.load(_validate_index)
    if damaged or journal.unpublished_snapshot_count():
        fail('METHOD_LIBRARY_REVIEW_REQUIRED')
    return saved or {}


def _bundle_root(journal, skill, revision):
    return checked_path(journal.directory/'bundles'/skill/revision,link_error='METHOD_SYMLINK',
                        volume_error='METHOD_VOLUME_REQUIRED')


def _installed(journal, skill, record, spec):
    repository,commit,paths,notice=spec
    if record['spec_sha256']!=digest(spec):
        return dict(status='METHOD_INSTALL_UPDATE_REQUIRED', handler=skill, source_commit=commit)
    bundle=_bundle_root(journal,skill,record['revision'])
    for path,expected in {'LICENSE':paths['LICENSE'],'NOTICE.md':hashlib.sha256(notice.encode()).hexdigest()}.items():
        checked=checked_path(bundle/path,link_error='METHOD_SYMLINK',volume_error='METHOD_VOLUME_REQUIRED')
        if not checked.is_file() or checked.stat().st_size>_MAX_FILE or hashlib.sha256(checked.read_bytes()).hexdigest()!=expected:
            return dict(status='SOURCE_VERSION_MISMATCH',handler=skill,dependency=path)
    result=external_study(skill,bundle)
    if result['status']=='SOURCE_METHOD_VERIFIED':
        result.update(installed=True, license='MIT', notice=str(bundle/'NOTICE.md'),
                      license_file=str(bundle/'LICENSE'), installed_version=commit)
    return result


def lookup(directory, skill):
    journal=_library(directory)
    if journal is None:
        return None
    with journal.locked(existing_only=True):
        record=_read_index(journal).get(skill)
        if record is None:
            return None
        if not record['active']:
            return dict(status='METHOD_DISABLED', handler=skill)
        return _installed(journal,skill,record,_spec(skill))


def _download_bundle(repository, commit, paths, deadline):
    def fetch(path):
        data = _fetch(repository, commit, path, deadline)
        if len(data) > _MAX_FILE:
            fail('METHOD_DOWNLOAD_SIZE_LIMIT')
        if hashlib.sha256(data).hexdigest() != paths[path]:
            fail('METHOD_DOWNLOAD_HASH_MISMATCH')
        try:
            data.decode('utf-8')
        except UnicodeError:
            fail('METHOD_DOWNLOAD_TEXT_REQUIRED')
        return data

    if len(paths) <= 4:
        return _bounded_contents(paths, map(fetch, paths))
    # Larger bundles keep the same per-file allowlist and shared deadline.
    # Batches bound memory even when the server responds out of order.
    names = list(paths)
    with ThreadPoolExecutor(max_workers=4) as pool:
        def batches():
            for offset in range(0, len(names), 4):
                yield from pool.map(fetch, names[offset:offset+4])
        return _bounded_contents(paths, batches())


def _bounded_contents(paths, payloads):
    contents = {}
    total = 0
    for name, data in zip(paths, payloads, strict=True):
        total += len(data)
        if total > _MAX_BUNDLE:
            fail('METHOD_DOWNLOAD_SIZE_LIMIT')
        contents[name] = data
    return contents


def install(directory,skill):
    spec=_spec(skill)
    _,commit,paths,notice=spec
    journal=_library(directory,create=True)
    with journal.locked():
        index=_read_index(journal)
        previous=index.get(skill)
        if previous:
            installed=_installed(journal,skill,previous,spec)
            if installed['status']=='SOURCE_METHOD_VERIFIED':
                if not previous['active']:
                    index[skill] = {**previous,'active':True}
                    journal.save(index,_validate_index)
                return dict(installed,status='METHOD_INSTALL_VERIFIED',reused=True,network_used=False)
        repository=spec[0]
        deadline=monotonic()+90
        contents=_download_bundle(repository,commit,paths,deadline)
        record=dict(revision=uuid4().hex,spec_sha256=digest(spec),active=True)
        bundle=_bundle_root(journal,skill,record['revision'])
        bundle.mkdir(parents=True,exist_ok=False,mode=0o700)
        contents['NOTICE.md']=notice.encode()
        for path,data in contents.items():
            target=bundle/path
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:
                stream.write(data)
        result=_installed(journal,skill,record,spec)
        if result['status']!='SOURCE_METHOD_VERIFIED':
            fail('METHOD_INSTALL_VALIDATION_FAILED')
        index[skill]=record
        journal.save(index,_validate_index)
        return dict(result,status='METHOD_INSTALL_VERIFIED',reused=False,network_used=True)


def disable(directory,skill):
    _spec(skill)
    journal=_library(directory)
    if journal is None:
        fail('METHOD_NOT_INSTALLED')
    with journal.locked(existing_only=True):
        index=_read_index(journal)
        row=index.get(skill)
        if row is None:
            fail('METHOD_NOT_INSTALLED')
        if row['active']:
            index[skill]={**row,'active':False}
            journal.save(index,_validate_index)
    return dict(status='METHOD_DISABLED',handler=skill,files_retained=True,network_used=False)
