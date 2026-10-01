"""Failures that require operator action before any more paid requests."""
import errno
import threading


class WorkflowStopped(RuntimeError):
    def __init__(self, message, *, reason):
        super().__init__(message)
        self.reason = reason


_lock = threading.RLock()
_failures = {}


def fatal_reason(error):
    if isinstance(error, WorkflowStopped):
        return error.reason
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return 'disk_full'
    status = getattr(error, 'status_code', None)
    text = str(error).lower()
    if any(word in text for word in ('insufficient_balance', 'insufficient balance',
                                     '余额不足', 'balance is too low')):
        return 'insufficient_balance'
    if status in (401, 403) or any(word in text for word in (
            'error code: 401', 'error code: 403', 'invalid_api_key', 'incorrect api key')):
        return 'provider_access'
    return None


def raise_if_fatal(error, job_id=None):
    reason = fatal_reason(error)
    if not reason:
        return
    failure = error if isinstance(error, WorkflowStopped) else WorkflowStopped(
        str(error), reason=reason)
    if job_id:
        with _lock:
            failure = _failures.setdefault(str(job_id), failure)
    if failure is error:
        raise failure
    raise failure from error


def failure(job_id):
    with _lock:
        return _failures.get(str(job_id))


def check(job_id):
    stopped = failure(job_id)
    if stopped is not None:
        raise stopped


def reset(job_id):
    with _lock:
        _failures.pop(str(job_id), None)
