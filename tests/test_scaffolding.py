"""Tests for scaffolding, domain constants, and manifest configuration."""

import json
import os

from custom_components.cast_studio.const import (
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
        "cast_studio",
        "manifest.json",
    )
    with open(manifest_path) as f:
        manifest = json.load(f)
    assert manifest["domain"] == "cast_studio"
    assert manifest["name"] == "CastStudio"
    assert "@javand" in manifest["codeowners"]
    assert "ffmpeg" in manifest["dependencies"]
    assert manifest["requirements"] == []


def test_constants():
    assert DOMAIN == "cast_studio"
    assert DEFAULT_OUTPUT_DIR == "/config/www/cast_studio"
    assert DEFAULT_HOST_VOICE == "Pulcherrima"
    assert DEFAULT_SCRIPTURE_VOICE == "Enceladus"


def test_async_setup_interfaces():
    from custom_components.cast_studio import async_setup, async_setup_entry

    assert callable(async_setup)
    assert callable(async_setup_entry)
