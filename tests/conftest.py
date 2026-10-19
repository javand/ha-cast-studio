"""Test fixtures for CastStudio integration tests."""

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: Any,
) -> None:
    """Enable custom integrations for all tests."""
    return
