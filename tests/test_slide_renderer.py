"""Tests for Pillow Slide Visual Renderer (Task 5)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from custom_components.automation_generator.slide_renderer import (
    PhoneticLeakError,
    render_slide,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_render_slide_dimensions_and_output(tmp_path: Path) -> None:
    """Test standard slide rendering dimensions, mode, and file output (from brief)."""
    output_path = str(tmp_path / "slide_01.jpg")
    card_data = {
        "title": "Good Morning, Javan!",
        "subtitle": "Saturday, October 17, 2026",
        "bullet_points": ["Sunny 72°F", "2 Events scheduled"],
    }
    result = render_slide(card_data, output_path)
    assert os.path.exists(result)
    assert result == output_path
    with Image.open(result) as img:
        assert img.size == (1024, 600)
        assert img.mode == "RGB"
        assert img.format == "JPEG"


def test_render_slide_phonetic_leak_in_title_raises(tmp_path: Path) -> None:
    """Assert that phonetic spelling in visual card title raises PhoneticLeakError."""
    output_path = str(tmp_path / "slide_leak_title.jpg")
    card_data = {
        "title": "Good Morning, Jay-ven!",
        "subtitle": "Saturday, October 17, 2026",
        "bullet_points": ["Sunny 72°F"],
    }
    with pytest.raises(
        (PhoneticLeakError, ValueError, AssertionError), match="Jay-ven"
    ):
        render_slide(card_data, output_path, phonetic_name="Jay-ven")

    assert not os.path.exists(output_path)


def test_render_slide_phonetic_leak_in_subtitle_raises(tmp_path: Path) -> None:
    """Assert that phonetic spelling in visual card subtitle raises PhoneticLeakError."""
    output_path = str(tmp_path / "slide_leak_subtitle.jpg")
    card_data = {
        "title": "Good Morning, Javan!",
        "subtitle": "Briefing for Jay-ven",
        "bullet_points": ["Sunny 72°F"],
        "phonetic_name": "Jay-ven",
    }
    with pytest.raises(
        (PhoneticLeakError, ValueError, AssertionError), match="Jay-ven"
    ):
        render_slide(card_data, output_path)

    assert not os.path.exists(output_path)


def test_render_slide_phonetic_leak_in_bullet_points_raises(tmp_path: Path) -> None:
    """Assert that phonetic spelling in bullet points raises PhoneticLeakError."""
    output_path = str(tmp_path / "slide_leak_bullet.jpg")
    card_data = {
        "title": "Good Morning, Javan!",
        "subtitle": "Saturday, October 17, 2026",
        "bullet_points": ["Reminder for Jay-ven: take meds", "Sunny 72°F"],
        "phonetic_name": "Jay-ven",
    }
    with pytest.raises(
        (PhoneticLeakError, ValueError, AssertionError), match="Jay-ven"
    ):
        render_slide(card_data, output_path)

    assert not os.path.exists(output_path)


def test_render_slide_phonetic_leak_via_recipient_dict(tmp_path: Path) -> None:
    """Assert that phonetic leak is detected when recipient dict is in card_data."""
    output_path = str(tmp_path / "slide_leak_recipient.jpg")
    card_data = {
        "title": "Good Morning, Jay-ven!",
        "recipient": {"name": "Javan", "phonetic_name": "Jay-ven"},
    }
    with pytest.raises(
        (PhoneticLeakError, ValueError, AssertionError), match="Jay-ven"
    ):
        render_slide(card_data, output_path)


def test_render_slide_same_name_and_phonetic_does_not_raise(tmp_path: Path) -> None:
    """When real name and phonetic name are identical, rendering should proceed normally."""
    output_path = str(tmp_path / "slide_same_name.jpg")
    card_data = {
        "title": "Good Morning, Sarah!",
        "subtitle": "Saturday, October 17, 2026",
        "bullet_points": ["Sunny 72°F"],
        "recipient": {"name": "Sarah", "phonetic_name": "Sarah"},
    }
    result = render_slide(card_data, output_path, phonetic_name="Sarah")
    assert os.path.exists(result)


def test_render_slide_creates_nested_parent_directory(tmp_path: Path) -> None:
    """Test that render_slide automatically creates parent directory if it does not exist."""
    nested_dir = tmp_path / "nested" / "output" / "dir"
    output_path = str(nested_dir / "slide.jpg")
    card_data = {
        "title": "Deep Nested Test",
        "bullet_points": ["Item 1"],
    }
    result = render_slide(card_data, output_path)
    assert os.path.exists(result)
    assert os.path.isdir(str(nested_dir))


def test_render_slide_empty_or_minimal_fields(tmp_path: Path) -> None:
    """Test rendering when optional fields like subtitle and bullet_points are missing."""
    output_path = str(tmp_path / "slide_minimal.jpg")
    card_data = {"title": "Only Title"}
    result = render_slide(card_data, output_path)
    assert os.path.exists(result)
    with Image.open(result) as img:
        assert img.size == (1024, 600)
        assert img.mode == "RGB"


def test_render_slide_text_wrapping_for_long_content(tmp_path: Path) -> None:
    """Test rendering very long text lines that require wrapping."""
    output_path = str(tmp_path / "slide_wrapping.jpg")
    card_data = {
        "title": "Good Morning, Javan! Here is a very long title that spans across multiple lines gracefully",
        "subtitle": "Saturday, October 17, 2026 — An exceptionally detailed morning briefing with high density text",
        "bullet_points": [
            "Weather update: Severe thunderstorm warning in effect until 11:00 AM with gusty winds and heavy rainfall expected.",
            "Calendar: 10:00 AM Quarterly Planning Session with the Architecture and Platform Engineering Group.",
            "Task: Review pull requests and verify end-to-end integration test passes.",
        ],
    }
    result = render_slide(card_data, output_path)
    assert os.path.exists(result)
    with Image.open(result) as img:
        assert img.size == (1024, 600)


def test_render_slide_invalid_data_type(tmp_path: Path) -> None:
    """Assert that passing a non-dict raises ValueError."""
    output_path = str(tmp_path / "slide_invalid.jpg")
    with pytest.raises(ValueError, match="card_data must be a dict"):
        render_slide(["not", "a", "dict"], output_path)  # type: ignore[arg-type]
