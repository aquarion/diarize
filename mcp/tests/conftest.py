import os
import tempfile

os.environ.setdefault("DIARIZE_LOG_DIR", tempfile.mkdtemp(prefix="diarize-test-logs-"))

import pytest  # noqa: E402
import server  # noqa: E402


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
