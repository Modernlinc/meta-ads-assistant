"""Use the host's IANA rules, with a pinned offline TZif fallback."""
from functools import lru_cache
import hashlib
from io import BytesIO
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BUNDLE_RELEASE = '2026f'
BUNDLE_RELATIVE_PATH = 'assets/iana-timezones/tzif.zip'
BUNDLE_PATH = Path(__file__).resolve().parents[2] / BUNDLE_RELATIVE_PATH
BUNDLE_SHA256 = 'b8a62627b1f41dbadc767e3248a36d10168890d4696735b22bcb91f390b71497'
BUNDLE_BYTES = 409113
ZONE_KEY = re.compile(r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*')


@lru_cache(maxsize=1)
def bundle_bytes(path):
    try:
        if path.is_symlink():
            raise ZoneInfoNotFoundError('BUNDLED_TZDATA_INVALID')
        with path.open('rb') as stream:
            data = stream.read(BUNDLE_BYTES+1)
    except OSError:
        raise ZoneInfoNotFoundError('BUNDLED_TZDATA_UNAVAILABLE') from None
    if len(data)!=BUNDLE_BYTES or hashlib.sha256(data).hexdigest()!=BUNDLE_SHA256:
        raise ZoneInfoNotFoundError('BUNDLED_TZDATA_INVALID')
    return data


@lru_cache(maxsize=256)
def bundled_zone(path, key):
    data = bundle_bytes(path)
    try:
        with ZipFile(BytesIO(data)) as archive, archive.open(key) as zone:
            return ZoneInfo.from_file(zone,key=key)
    except (KeyError, BadZipFile, ValueError):
        raise ZoneInfoNotFoundError(key) from None


def resolve_zone(key):
    if type(key) is not str or len(key)>128 or not ZONE_KEY.fullmatch(key):
        raise ZoneInfoNotFoundError('INVALID_IANA_ZONE_KEY')
    try:
        return ZoneInfo(key), 'HOST_ZONEINFO'
    except ZoneInfoNotFoundError:
        return bundled_zone(BUNDLE_PATH,key), 'BUNDLED_TZIF'


def load_zone(key):
    return resolve_zone(key)[0]
