import os
import tempfile

os.environ.setdefault("DIARIZE_LOG_DIR", tempfile.mkdtemp(prefix="diarize-test-logs-"))

import pytest  # noqa: E402
import server  # noqa: E402


@pytest.fixture(autouse=True)
def clear_jobs():
    server.jobs.clear()
    yield
    server.jobs.clear()
