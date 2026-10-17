"""Tests for scaffolding, domain constants, and manifest configuration."""

import json
import os

from custom_components.automation_generator.const import (
    DEFAULT_HOST_VOICE,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SCRIPTURE_VOICE,
    DOMAIN,
)


def test_manifest_metadata():
    manifest_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "custom_components",
        "automation_generator",
        "manifest.json",
    )
    with open(manifest_path) as f:
        manifest = json.load(f)
    assert manifest["domain"] == "automation_generator"
    assert manifest["name"] == "Automation Generator"
    assert "@javand" in manifest["codeowners"]
    assert "ffmpeg" in manifest["dependencies"]
    assert any("pillow" in req.lower() for req in manifest["requirements"])


def test_constants():
    assert DOMAIN == "automation_generator"
    assert DEFAULT_OUTPUT_DIR == "/config/www/automations"
    assert DEFAULT_HOST_VOICE == "Pulcherrima"
    assert DEFAULT_SCRIPTURE_VOICE == "Enceladus"


def test_async_setup_interfaces():
    from custom_components.automation_generator import async_setup, async_setup_entry

    assert callable(async_setup)
    assert callable(async_setup_entry)
