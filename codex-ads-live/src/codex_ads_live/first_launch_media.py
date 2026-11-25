"""One approved file, durable upload phases, and read-only media recovery."""

from hashlib import sha256
import os
from pathlib import Path
import re
import ssl
import stat
from time import monotonic
from urllib.parse import urlencode
from uuid import uuid4

from .graph_network import HOST, VIDEO_HOST, graph_https_connection
from .http_read import arm_http_deadline
from .journal import digest
from .local_paths import checked_path
from .validation import InputError, fail, object_only, strict_json

LIMITS = {'IMAGE': 10 * 1024**2, 'VIDEO': 100 * 1024**2}
CHUNK_LIMIT = 16 * 1024**2
IDENTIFIERS = ('workspace_sha256', 'plan_sha256', 'business_id', 'app_id', 'account_id', 'page_id')
EXTENSIONS = {'IMAGE': ('.jpg', '.jpeg', '.png'), 'VIDEO': ('.mp4', '.mov')}
NATIVE_ID = re.compile(r'[1-9][0-9]{0,39}')
IMAGE_HASH = re.compile(r'[A-Za-z0-9_-]{1,128}')
HEX = re.compile(r'[a-f0-9]{64}')


def file_bytes(path, media_type, *, owner=None):
    path = checked_path(path, link_error='FIRST_LAUNCH_MEDIA_PATH_LINK',
                        volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
    if media_type not in LIMITS:
        fail('FIRST_LAUNCH_MEDIA_TYPE')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or (owner is not None and info.st_uid != owner)
                or not 0 < info.st_size <= LIMITS[media_type]):
            fail('FIRST_LAUNCH_MEDIA_FILE_INVALID')
        data = source.read(info.st_size + 1)
    if len(data) != info.st_size:
        fail('FIRST_LAUNCH_MEDIA_FILE_CHANGED')
    return data


def media_scope(identity, media_type, path, data):
    ext = Path(path).suffix.lower()
    scope = dict(identity, media_type=media_type, file_size=len(data), asset_sha256=sha256(data).hexdigest(),
                 filename='creative' + ext, schema='first-launch-media-v1')
    validate_scope(scope)
    return scope


def validate_scope(scope):
    fields = (*IDENTIFIERS, 'schema', 'media_type', 'filename', 'file_size', 'asset_sha256')
    object_only(scope, fields, fields)
    kind = scope['media_type']
    if (scope['schema'] != 'first-launch-media-v1' or kind not in LIMITS
            or scope['filename'] not in {'creative' + ext for ext in EXTENSIONS[kind]}
            or type(scope['file_size']) is not int or not 0 < scope['file_size'] <= LIMITS[kind]
            or type(scope['asset_sha256']) is not str or not HEX.fullmatch(scope['asset_sha256'])):
        fail('FIRST_LAUNCH_MEDIA_SCOPE_INVALID')


def verify_bytes(scope, data):
    if len(data) != scope['file_size'] or sha256(data).hexdigest() != scope['asset_sha256']:
        fail('FIRST_LAUNCH_MEDIA_FILE_CHANGED')


def _offset(raw, size):
    if type(raw) is str and re.fullmatch(r'[0-9]{1,12}', raw):
        raw = int(raw)
    if type(raw) is not int or not 0 <= raw <= size:
        fail('FIRST_LAUNCH_VIDEO_OFFSET_INVALID')
    return raw


def upload(scope, data, send, record):
    validate_scope(scope)
    verify_bytes(scope, data)
    base = '/v27.0/act_' + scope['account_id']
    if scope['media_type'] == 'IMAGE':
        record('IMAGE_INTENT', {})
        reply = send(HOST, base+'/adimages', {}, ('filename', scope['filename'], data))
        images = reply.get('images')
        if type(images) is not dict or set(images) != {scope['filename']}:
            fail('FIRST_LAUNCH_IMAGE_RECEIPT_MISMATCH')
        value = images[scope['filename']]
        image = value.get('hash') if type(value) is dict else None
        if type(image) is not str or not IMAGE_HASH.fullmatch(image):
            fail('FIRST_LAUNCH_IMAGE_RECEIPT_MISMATCH')
        asset = dict(status='ACKNOWLEDGED', image_hash=image)
        record('IMAGE_ACK', asset)
        return asset

    path = base + '/advideos'
    size = len(data)
    record('VIDEO_START_INTENT', {})
    started = send(VIDEO_HOST, path, {'upload_phase': 'start', 'file_size': size}, None)
    session, video = started.get('upload_session_id'), started.get('video_id')
    if any(type(value) is not str or not NATIVE_ID.fullmatch(value) for value in (session, video)):
        fail('FIRST_LAUNCH_VIDEO_START_RECEIPT_INVALID')
    original = dict(video_id=video, upload_session_id=session)
    record('VIDEO_STARTED', original)
    offsets = started
    sent = 0
    for index in range(129):
        start = _offset(offsets.get('start_offset'), size)
        end = _offset(offsets.get('end_offset'), size)
        if start == end == sent == size:
            break
        if index == 128:
            fail('FIRST_LAUNCH_VIDEO_CHUNK_LIMIT')
        if start != sent or not 0 < end - start <= CHUNK_LIMIT:
            fail('FIRST_LAUNCH_VIDEO_OFFSET_INVALID')
        record('VIDEO_TRANSFER_INTENT', dict(original, index=index, start_offset=start, end_offset=end))
        offsets = send(VIDEO_HOST, path, dict(upload_phase='transfer', upload_session_id=session, start_offset=start),
                       ('video_file_chunk', scope['filename'], data[start:end]))
        sent = end
    record('VIDEO_FINISH_INTENT', original)
    finished = send(VIDEO_HOST, path, dict(upload_phase='finish', upload_session_id=session, title=scope['filename']), None)
    if finished.get('success') is not True:
        fail('FIRST_LAUNCH_VIDEO_FINISH_UNCONFIRMED')
    asset = dict(original, status='PROCESSING')
    record('VIDEO_FINISH_ACK', asset)
    return asset


def read_media(account_id, asset, get):
    if 'image_hash' in asset:
        from json import dumps
        image = asset['image_hash']
        if type(image) is not str or not IMAGE_HASH.fullmatch(image):
            fail('FIRST_LAUNCH_IMAGE_RECEIPT_MISMATCH')
        query = urlencode(dict(fields='hash', hashes=dumps([image]), limit=2))
        result = get('/v27.0/act_' + account_id + '/adimages?' + query)
        rows = result.get('data')
        if (type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict
                or rows[0].get('hash') != image or (result.get('paging') or {}).get('next')):
            fail('FIRST_LAUNCH_LOCAL_ACCOUNT_IMAGE_UNVERIFIED')
        return dict(image_hash=image, status='READY')
    video = asset.get('video_id')
    if type(video) is not str or not NATIVE_ID.fullmatch(video):
        fail('FIRST_LAUNCH_VIDEO_RECEIPT_REQUIRED')
    result = get('/v27.0/' + video + '?fields=id,status')
    status = result.get('status')
    if result.get('id') != video or type(status) is not dict:
        fail('FIRST_LAUNCH_VIDEO_READBACK_MISMATCH')
    observed = {'ready': 'READY', 'processing': 'PROCESSING', 'error': 'PROCESSING_FAILED'}.get(
        status.get('video_status'), 'UNKNOWN')
    return dict(video_id=video, status=observed)


def _multipart(fields, part):
    boundary = uuid4().hex
    chunks = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
              for name, value in fields.items()]
    name, filename, data = part
    chunks.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                   'Content-Type: application/octet-stream\r\n\r\n'.encode(), data, b'\r\n',
                   f'--{boundary}--\r\n'.encode()))
    return 'multipart/form-data; boundary=' + boundary, b''.join(chunks)


def post_media(host, path, fields, part, token, remaining):
    if part is None:
        content_type, body = 'application/x-www-form-urlencoded', urlencode(fields).encode()
    else:
        content_type, body = _multipart(fields, part)
    budget = remaining()
    if budget <= 0:
        fail('FIRST_LAUNCH_MEDIA_DEADLINE')
    end = monotonic() + budget
    connection = graph_https_connection(budget, ssl.create_default_context(), host=host)
    try:
        connection.connect()
        budget = min(remaining(), end-monotonic())
        if budget <= 0:
            fail('FIRST_LAUNCH_MEDIA_DEADLINE')
        arm_http_deadline(connection, budget)
        connection.sock.settimeout(budget)
        connection.request('POST', path, body=body, headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': content_type, 'Accept': 'application/json'})
        with connection.getresponse() as response:
            if not 200 <= response.status < 300:
                fail('FIRST_LAUNCH_MEDIA_HTTP_UNCONFIRMED')
            raw = response.read(128*1024 + 1)
        if len(raw) > 128*1024:
            fail('FIRST_LAUNCH_MEDIA_RECEIPT_LIMIT')
        result = strict_json(raw.decode('utf-8'), max_chars=128*1024)
        if type(result) is not dict or 'error' in result:
            fail('FIRST_LAUNCH_MEDIA_HTTP_UNCONFIRMED')
        return result
    finally:
        connection.close()


def run(workspace, directory, file, kind, packet, mode):
    from .first_launch_authority import read_key
    from .first_launch_local_host import LocalFirstLaunchHost, scope_from_launch
    from .first_launch_plan import _read_plan_json
    from .first_launch_progress import progress
    from .journal import Journal
    from .meta_write_mapping import utcnow

    launch = progress(workspace, directory)
    identity = scope_from_launch(launch)
    if mode == 'plan':
        if file is None or kind is None or packet is not None:
            fail('FIRST_LAUNCH_MEDIA_FILE_AND_TYPE_REQUIRED')
        scoped = media_scope(identity, kind, file, file_bytes(file, kind))
        selected = launch.get('creative_research_batch') or [launch.get('creative_research')]
        if selected[0] is not None and scoped['asset_sha256'] not in {item['asset_sha256'] for item in selected}:
            fail('FIRST_LAUNCH_RESEARCH_ASSET_CHANGED')
        return dict(media_scope=scoped, scope_sha256=digest(scoped), network_used=False,
                    required_permission='INDEPENDENT_MEDIA_APPROVAL', write_authorized=False)
    if packet is None or file is not None or kind is not None:
        fail('FIRST_LAUNCH_MEDIA_ORIGINAL_PACKET_REQUIRED')
    scoped = _read_plan_json(packet)['media_scope']
    validate_scope(scoped)
    if any(scoped[key] != val for key, val in identity.items()):
        fail('FIRST_LAUNCH_MEDIA_SCOPE_CHANGED')
    key = digest(scoped)
    host = LocalFirstLaunchHost(identity)
    location = Path(workspace) / 'managed-media' / key
    journal = Journal(location, stage_snapshots=True, existing_only=mode=='recover')

    def validate(row):
        object_only(row, ('scope_sha256', 'attempt_id', 'reserved_at', 'scope'), ('scope_sha256', 'attempt_id', 'reserved_at', 'scope'))
        if (row['scope'] != scoped or row['scope_sha256'] != key
                or type(row['attempt_id']) is not str or not HEX.fullmatch(row['attempt_id'])):
            fail('FIRST_LAUNCH_MEDIA_ATTEMPT_SCOPE_CHANGED')

    with journal.locked(existing_only=mode=='recover'):
        row, damaged = journal.load(validate)
        if damaged or journal.unpublished_snapshot_count():
            fail('FIRST_LAUNCH_MEDIA_JOURNAL_REVIEW_REQUIRED')
        if mode == 'dispatch':
            if row is not None:
                fail('FIRST_LAUNCH_MEDIA_ORIGINAL_ATTEMPT_RECOVERY_ONLY')
            approval = host.rpc('get', 'media', key)
            row = dict(scope_sha256=key, scope=scoped, attempt_id=approval['attempt_id'],
                       reserved_at=utcnow().isoformat())
            journal.save(row, validate)
            try:
                asset = host.rpc('execute', 'media', key, row['attempt_id'])
            except (InputError, OSError):
                return dict(state='OUTCOME_UNKNOWN', **row)
            return dict(state=asset['status'], **row)
        if row is None:
            fail('FIRST_LAUNCH_MEDIA_ATTEMPT_REQUIRED')
        asset = host.rpc('receipt', 'media', key, row['attempt_id'])
        if 'image_hash' not in asset and 'video_id' not in asset:
            return dict(state=asset['status'], **row)
        lease_key = read_key(identity, 'media', key)
        lease = host._validated_lease('read', lease_key)
        observed = read_media(identity['account_id'], asset, lambda path: host._get(
            path, lease, assert_current=lambda: host.assert_lease(lease, 'read', lease_key)))
        return dict(state=observed['status'], media=observed, **row, network_used=True)
