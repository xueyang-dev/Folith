"""OS lock behaviour on new, empty lock files (no initialisation write)."""
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest

from folith import job_lock


def test_new_lock_releases_on_close(tmp_path):
    path = tmp_path / 'new.lock'
    holder = job_lock.acquire(path)
    try:
        assert path.stat().st_size == 0
        assert job_lock.acquire(path, blocking=False) is None
    finally:
        holder.close()
    with job_lock.exclusive(path):
        assert path.stat().st_size == 0


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows mandatory byte locks')
def test_empty_file_contention_waits_for_lock_instead_of_writing(tmp_path):
    import msvcrt

    path = tmp_path / 'empty.lock'
    # Reproduce the vulnerable initialisation state: byte zero is locked by
    # another handle while the file is still empty. Windows permits this.
    holder = path.open('a+b')
    msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, 1)
    started = Event()

    def acquire():
        started.set()
        return job_lock.acquire(path)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(acquire)
        try:
            assert started.wait(timeout=5)
            # A blocking acquire must wait, not fail early trying to flush NUL
            # into the other handle's mandatory byte lock.
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        finally:
            holder.close()
        acquired = future.result(timeout=5)
        acquired.close()
    assert path.stat().st_size == 0
