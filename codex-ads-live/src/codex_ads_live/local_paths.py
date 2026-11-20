"""Validate local storage before use; not protection against hostile FS races."""
import os
from pathlib import Path
from .validation import fail


def checked_path(value, *, link_error, volume_error):
    path = Path(value).absolute()
    # Inspect the spelling before resolve: links hidden by '..' still matter.
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            fail(link_error)
    resolved = path.resolve()
    # Both the supplied volume and the actual destination must be mounted.
    for candidate in (path, resolved):
        if os.name == 'posix' and len(candidate.parts) > 2 and candidate.parts[1] == 'Volumes':
            if not os.path.ismount(Path(*candidate.parts[:3])): fail(volume_error)
        if os.name == 'nt' and not Path(candidate.anchor).is_dir(): fail(volume_error)
    return resolved
