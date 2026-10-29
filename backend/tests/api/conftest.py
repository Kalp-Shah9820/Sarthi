"""The API tests' client: a copy of the seeded database with one completed run, per test module."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from sarthi.api.main import create_app
from sarthi.orchestrator.runner import run_pipeline
from tests.conftest import private_database


@pytest.fixture(scope="module")
def client(tmp_path_factory, template_db, analysis):
    """A copy of the seeded database with one completed run, and a client whose event loop lasts the module.

    Module scope: each file starts from the same state, whatever another file did to its own copy.
    """
    with private_database(tmp_path_factory, template_db, "api", analysis=analysis):
        asyncio.run(run_pipeline("test"))
        with TestClient(create_app()) as test_client:
            yield test_client
