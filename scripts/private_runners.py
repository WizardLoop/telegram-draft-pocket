"""Keep emitted environments outside source inventory and uploaded evidence."""

import os
from pathlib import Path
import re
import stat
import tempfile

import package


class PrivateRunners:
    """Own one private directory containing only registered scripts and markers.

    Call cleanup after native evidence extraction, including failure paths.
    No checkout exclusions or recursive deletion are needed.
    """

    def __init__(self, checkout, artifacts, *, temporary_parent=None):
        self._forbidden = (Path(checkout).resolve(strict=True), Path(artifacts).resolve(strict=True))
        self._base = Path(temporary_parent if temporary_parent is not None else tempfile.gettempdir()).resolve(strict=True)
        if not self._base.is_dir() or any(self._base.is_relative_to(root) for root in self._forbidden):
            raise package.PackageError("Private runner temporary parent must be outside checkout and artifacts")
        self.path = Path(tempfile.mkdtemp(prefix="draft-pocket-runners-", dir=self._base)).resolve(strict=True)
        self._identity = (self.path.stat().st_dev, self.path.stat().st_ino)
        self._names = set()
        self._cleaned = False
        self._verify_directory()

    def _verify_directory(self):
        info = self.path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or self.path.is_symlink()
                or getattr(self.path, "is_junction", lambda: False)()
                or self.path.parent != self._base or self.path.resolve(strict=True) != self.path
                or any(self.path.is_relative_to(root) for root in self._forbidden)
                or (info.st_dev, info.st_ino) != self._identity):
            raise package.PackageError("Private runner directory ownership/path changed; refusing cleanup")
        if os.name == "posix" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise package.PackageError("Private runner directory must remain owned and mode 0700")

    def script_path(self, name):
        if self._cleaned or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*\.sh", name):
            raise package.PackageError("Invalid or closed private runner filename")
        self._verify_directory()
        if name in self._names or (self.path / name).exists() or (self.path / name).is_symlink():
            raise package.PackageError("Private runner path must be fresh")
        self._names.add(name)
        return self.path / name

    def cleanup(self):
        if self._cleaned:
            return
        self._verify_directory()
        allowed = self._names | {name + ".started" for name in self._names}
        entries = list(self.path.iterdir())
        # Validate the entire small directory before deleting any entry. In
        # particular, never descend into a substituted directory or follow links.
        for entry in entries:
            if (entry.name not in allowed or not stat.S_ISREG(entry.lstat().st_mode)
                    or entry.is_symlink() or getattr(entry, "is_junction", lambda: False)()):
                raise package.PackageError("Private runner directory contains an unexpected entry; refusing cleanup")
        for entry in entries:
            self._verify_directory()
            entry.unlink()
        self._verify_directory()
        self.path.rmdir()
        self._cleaned = True
