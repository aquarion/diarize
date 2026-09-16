import os
import tempfile

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
    server.jobs.clear()
    server._pending_eviction.clear()
    with server._registry_lock:
        server.JOBS_FILE.unlink(missing_ok=True)
