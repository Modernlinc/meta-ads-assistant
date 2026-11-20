"""Start the separately administered macOS first-launch authority."""

import os
from pathlib import Path
import stat
import sys


def _check_admin_node(path):
    entry = path.lstat()
    if (entry.st_uid != 0 or stat.S_ISLNK(entry.st_mode) or entry.st_mode & 0o022
            or not (stat.S_ISREG(entry.st_mode) or stat.S_ISDIR(entry.st_mode))):
        raise SystemExit('FIRST_LAUNCH_ADMIN_INSTALLATION_REQUIRED')


if __name__ == '__main__':
    if (sys.version_info < (3, 12) or sys.platform != 'darwin' or os.geteuid() != 0
            or not sys.flags.isolated or not sys.dont_write_bytecode):
        raise SystemExit('FIRST_LAUNCH_MACOS_ADMIN_ISOLATED_PYTHON_REQUIRED')
    entry = Path(__file__).absolute()
    for node in (*reversed(entry.parents), entry):
        _check_admin_node(node)
    interpreter = Path(sys.executable).resolve(strict=True)
    for node in (*reversed(interpreter.parents), interpreter):
        _check_admin_node(node)
    source = entry.parent / 'src'
    _check_admin_node(source)
    # Validate the entire import tree before importing any administrator code.
    for directory, children, files in os.walk(source, followlinks=False):
        for name in (*children, *files):
            _check_admin_node(Path(directory) / name)
    sys.path.insert(0, str(source))
    from codex_ads_live.first_launch_authority import main
    sys.exit(main())
