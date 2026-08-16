from __future__ import annotations

import os

import pytest


def pytest_addoption(parser) -> None:
    parser.getgroup("editable-ppt-live").addoption(
        "--run-live-app-server",
        action="store_true",
        default=False,
        help="run tests that call the installed Codex App Server",
    )


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "live_app_server: requires explicit live Codex App Server access and is skipped by default",
    )


def pytest_collection_modifyitems(config, items) -> None:
    enabled = (
        config.getoption("--run-live-app-server")
        and os.environ.get("EDITABLE_PPT_RUN_LIVE_APP_SERVER_TESTS") == "1"
    )
    if enabled:
        return
    skip = pytest.mark.skip(
        reason=(
            "live App Server tests require both --run-live-app-server and "
            "EDITABLE_PPT_RUN_LIVE_APP_SERVER_TESTS=1"
        )
    )
    for item in items:
        if item.get_closest_marker("live_app_server") is not None:
            item.add_marker(skip)
