import os
import tempfile
import threading

os.environ.setdefault("DIARIZE_LOG_DIR", tempfile.mkdtemp(prefix="diarize-test-logs-"))

import pytest  # noqa: E402
import server  # noqa: E402

# Several tests simulate a registry write failure by monkeypatching
# _update_job_record/_write_registry to always fail. A Job's finalizer
# thread (started as soon as it's constructed) races those tests
# independently of anything they explicitly wait for, retrying with the
# *production* defaults (5 attempts, 1s apart - up to 4s) unless overridden.
# If a test returns before that thread gives up, pytest's monkeypatch
# reverts mid-retry, so the thread's next attempt hits the *real*
# _update_job_record - which now finds a genuine record and succeeds -
# and writes to whatever jobs.json the *next* test has by then created.
# Small session-wide defaults close this whole class of cross-test leakage;
# a test that needs to observe the retry mechanism itself still overrides
# these explicitly via monkeypatch.
server._PERSIST_RETRY_ATTEMPTS = 2
server._PERSIST_RETRY_DELAY = 0.01

# Tracks every Job ever constructed during the test session, regardless of
# whether it ends up in server.jobs (a test may hold one only as a local
# variable, or it may already have evicted itself by the time a fixture
# looks). Job.__post_init__ starts its finalizer thread immediately, so this
# is the only reliable way to wait for *every* such thread to finish before
# tearing down - waiting on server.jobs.values() alone would miss a job that
# was never inserted, or one that already evicted itself.
_all_jobs: list[server.Job] = []
_all_jobs_lock = threading.Lock()
_orig_post_init = server.Job.__post_init__


def _tracking_post_init(self: server.Job) -> None:
    with _all_jobs_lock:
        _all_jobs.append(self)
    _orig_post_init(self)


server.Job.__post_init__ = _tracking_post_init


@pytest.fixture(autouse=True)
def clear_jobs():
    # Held with _registry_lock: a previous test's background finalizer
    # thread may still be between loading the registry and os.replace()-ing
    # it, and an unsynchronized unlink here could be clobbered by that late
    # write, leaking a stray record into the next test.
    server.jobs.clear()
    server._pending_eviction.clear()
    with server._registry_lock:
        server.JOBS_FILE.unlink(missing_ok=True)
    yield
    # Wait for every finalizer thread this test's Jobs started before
    # unlinking below - otherwise one still mid-retry can wake up after the
    # unlink, land a write on the *next* test's jobs.json, and leak a stray
    # record into it (see _all_jobs above).
    with _all_jobs_lock:
        pending, _all_jobs[:] = list(_all_jobs), []
    for job in pending:
        job._finalizer_done.wait(timeout=2.0)
    server.jobs.clear()
    server._pending_eviction.clear()
    with server._registry_lock:
        server.JOBS_FILE.unlink(missing_ok=True)
