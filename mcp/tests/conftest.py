import pytest
import server


@pytest.fixture(autouse=True)
def clear_jobs():
    server.jobs.clear()
    yield
    server.jobs.clear()
