"""OS-owned locks for worker lifetime and runtime read/merge/write updates."""
from contextlib import contextmanager
import os

from .workflow_errors import raise_if_fatal


def acquire(path, *, blocking=True):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open('a+b')
    except OSError as error:
        raise_if_fatal(error, path.parent.name)
        raise
    try:
        if os.name == 'nt':
            import msvcrt
            # Windows can lock bytes beyond EOF. Initialising the file first
            # races with another opener that has already locked byte zero:
            # its mandatory lock would reject our delayed buffered write.
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except (BlockingIOError, PermissionError):
        stream.close()
        if not blocking:
            return None
        raise
    except BaseException:
        stream.close()
        raise
    return stream


@contextmanager
def exclusive(path):
    stream = acquire(path)
    try:
        yield
    finally:
        stream.close()
