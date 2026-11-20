"""Append-only local synthetic state with an OS-released single-writer lock.

Checksums detect accidental corruption, not malicious modification. No secrets,
customer payloads or credential material are accepted by the runtime schema.
"""
from contextlib import contextmanager
import hashlib
import json
import os
import errno
from pathlib import Path
import re
from threading import get_ident
import time
from uuid import uuid4
from .validation import fail, strict_json
from .local_paths import checked_path


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)


def digest(value): return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _verify_marker(marker, expected):
    content = canonical(expected).encode('utf-8')
    deadline = time.monotonic()+.5
    while True:
        if marker.is_symlink() or not marker.is_file():
            fail('STATE_DIRECTORY_NOT_OWNED')
        with marker.open('rb') as handle:
            data = handle.read(1001)
        if data == content:
            return
        if not content.startswith(data):
            try:
                if len(data)<=1000 and strict_json(data.decode('utf-8')) == expected:
                    return
            except ValueError:
                pass
            fail('STATE_DIRECTORY_NOT_OWNED')
        # An exclusive creator can be between open and the first write.
        remaining = deadline-time.monotonic()
        if remaining<=0:
            fail('STATE_DIRECTORY_NOT_OWNED')
        time.sleep(min(.01,remaining))


class Journal:
    def __init__(self,directory,mode='MOCK',*,existing_only=False,stage_snapshots=False):
        if mode not in ('MOCK','READ_ONLY'): fail('STATE_MODE')
        self.mode=mode
        self.directory=checked_path(directory, link_error='STATE_PATH_SYMLINK', volume_error='STATE_VOLUME_NOT_MOUNTED')
        self.stage_snapshots=stage_snapshots
        self._lock_thread=None
        marker=self.directory/"operator-state.json"
        expected={"owner":"codex-ads-live","schema":1,"mode":mode}
        if existing_only:
            _verify_marker(marker,expected)
            return
        if self.directory.exists() and not marker.exists() and any(self.directory.iterdir()):
            # The directory listing may have observed a concurrent creator.
            if not marker.exists():
                fail("STATE_DIRECTORY_NOT_OWNED")
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:
            with marker.open("x",encoding="utf-8") as handle:
                handle.write(canonical(expected));handle.flush();os.fsync(handle.fileno())
        except FileExistsError:
            _verify_marker(marker,expected)

    @contextmanager
    def locked(self, wait_seconds=0, *, existing_only=False):
        if type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= 1: fail('STATE_LOCK_WAIT_BUDGET')
        self._check_directory()
        path=self.directory/"operator.lock"
        if path.is_symlink(): fail("STATE_LOCK_SYMLINK")
        flags=os.O_RDWR|getattr(os,"O_NOFOLLOW",0)
        if not existing_only:
            flags |= os.O_CREAT
        fd=os.open(path,flags,0o600)
        handle=os.fdopen(fd,"r+b")
        acquired=False
        try:
            deadline=time.monotonic()+wait_seconds
            def acquire(operation):
                while True:
                    try:
                        operation();return
                    except OSError as exc:
                        if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK): fail('STATE_LOCK_FAILURE')
                        remaining=deadline-time.monotonic()
                        if remaining<=0: fail('STATE_BUSY')
                        time.sleep(min(.01,remaining))
            if os.name=="nt":
                import msvcrt
                # The CRT can lock past EOF. Avoid writing before acquiring the lock.
                handle.seek(0)
                acquire(lambda: msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1))
            else:
                import fcntl
                acquire(lambda: fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB))
            acquired=True
            self._lock_thread=get_ident()
            yield self
        finally:
            try:
                if acquired:
                    self._lock_thread=None
                    if os.name=="nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                    else:
                        fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
            finally:
                handle.close()

    def locked_by_current_thread(self):
        return self._lock_thread==get_ident()

    def _check_directory(self):
        checked_path(self.directory, link_error='STATE_PATH_SYMLINK', volume_error='STATE_VOLUME_NOT_MOUNTED')

    def snapshots(self):
        self._check_directory()
        result=[]
        for path in self.directory.glob("snapshot-*.json"):
            if not re.fullmatch(r"snapshot-[0-9]{10}\.json",path.name): fail("INVALID_SNAPSHOT_NAME")
            if path.is_symlink(): fail("STATE_SNAPSHOT_SYMLINK")
            result.append(path)
        return sorted(result)

    def unpublished_snapshot_count(self):
        self._check_directory()
        return sum(1 for path in self.directory.glob('.pending-snapshot-*.json')
                   if re.fullmatch(r'\.pending-snapshot-[0-9]{10}-[a-f0-9]{32}\.json',path.name))

    @staticmethod
    def read_snapshot(path, validator):
        if path.is_symlink(): fail('STATE_SNAPSHOT_SYMLINK')
        if not path.is_file(): fail('STATE_SNAPSHOT_FILE_REQUIRED')
        with path.open('rb') as stream:
            raw = stream.read(2_000_001)
        if len(raw)>2_000_000: fail('STATE_TOO_LARGE')
        envelope = strict_json(raw.decode('utf-8'))
        if type(envelope) is not dict or set(envelope)!={'payload','sha256'}: fail('STATE_ENVELOPE')
        data = envelope['payload']
        if digest(data)!=envelope['sha256']: fail('STATE_CHECKSUM')
        validator(data)
        return data, envelope['sha256']

    def load(self,validator):
        damaged=[]
        for path in reversed(self.snapshots()):
            try:
                data, _ = self.read_snapshot(path, validator)
                return data,damaged
            except (ValueError,TypeError,UnicodeError,OverflowError):
                damaged.append(path.name)
        if damaged: fail("NO_VALID_STATE")
        return None,[]

    def save(self,payload,validator,*,before_write=None):
        validator(payload)
        snapshots=self.snapshots()
        sequence=(int(snapshots[-1].stem.split("-")[1])+1) if snapshots else 1
        if sequence>=10**10: fail("STATE_SEQUENCE_EXHAUSTED")
        path=self.directory/f"snapshot-{sequence:010}.json"
        staging=(self.directory/f'.pending-snapshot-{sequence:010}-{uuid4().hex}.json' if self.stage_snapshots else path)
        content=canonical({"payload":payload,"sha256":digest(payload)})
        self._check_directory()
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0)
        if before_write is not None:
            before_write()
        fd=os.open(staging,flags,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as handle:
            handle.write(content)
            handle.flush();os.fsync(handle.fileno())
        if self.stage_snapshots:
            # The monitor holds the writer lock. Publish only a flushed file
            # into its unused sequence slot and leave interrupted attempts intact.
            self._check_directory()
            if os.path.lexists(path): fail('STATE_SNAPSHOT_SEQUENCE_COLLISION')
            os.rename(staging,path)
        # POSIX directory fsync covers the new filename; unsupported filesystems
        # report a controlled failure rather than claiming durable success.
        if os.name!="nt":
            self._check_directory()
            fd=os.open(self.directory,os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        return path
