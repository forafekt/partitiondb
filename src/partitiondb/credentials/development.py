"""Local development secret provider. Deployments should supply their own vault.

Hashed references, exclusive 0600 files, fsynced writes and a private directory.
The folder must not be served, committed or placed in the package distribution.
"""

import os
import stat
from hashlib import sha256
from pathlib import Path


class DevelopmentCredentials:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, reference):
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("A credential reference is required")
        if self.directory.is_symlink():
            raise ValueError("Credential directory must not be a symlink")
        if self.directory.exists():
            info = self.directory.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise ValueError("Credential directory must be private and owned by this user")
        return self.directory / sha256(reference.encode()).hexdigest()

    def _sync_directory(self):
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def put(self, reference, password):
        if not isinstance(password, str) or not password:
            raise ValueError("A nonempty credential is required")
        path = self._path(reference)
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            parent = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        self._path(reference)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(password)
            stream.flush()
            os.fsync(stream.fileno())
        self._sync_directory()

    def get(self, reference):
        fd = os.open(self._path(reference), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise ValueError("Credential file must be private and owned by this user")
            return stream.read()

    def delete(self, reference):
        path = self._path(reference)
        if path.is_symlink():
            raise ValueError("Credential file must not be a symlink")
        try:
            path.unlink()
        except FileNotFoundError:
            return
        self._sync_directory()

    def absent(self, reference):
        path = self._path(reference)
        try:
            path.lstat()
        except FileNotFoundError:
            return True
        return False
