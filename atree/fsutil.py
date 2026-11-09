"""Atomic file replacement."""
import os
import threading


def atomic_write(path, data, mode=None, *, fsync=False):
    """Replace ``path`` with ``data`` (str or bytes) in one rename."""
    path = os.fspath(path)
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, ".%s.%d.%d.tmp" % (
        os.path.basename(path), os.getpid(), threading.get_ident()))
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                     0o666 if mode is None else 0o600)
    except FileExistsError:
        # left over from an interrupted write: drop it so the next call succeeds
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            if mode is not None:
                os.fchmod(f.fileno(), mode)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
