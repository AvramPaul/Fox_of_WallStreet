"""Testele marcate `live` (rețea reală) rulează doar cu `pytest -m live`."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "live: test cu date reale din rețea (rulează cu -m live)")


def pytest_collection_modifyitems(config, items):
    if config.option.markexpr:
        return
    skip_live = pytest.mark.skip(reason="test live — rulează cu `pytest -m live`")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
