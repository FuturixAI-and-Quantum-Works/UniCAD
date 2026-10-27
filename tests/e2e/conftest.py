import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: end-to-end scenario over MCP stdio; runs only with UNICAD_E2E=1")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("UNICAD_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="end-to-end scenarios run only with UNICAD_E2E=1")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
