"""Tests for Pillow Slide Visual Renderer (Task 5)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from custom_components.cast_studio.slide_renderer import (
    PhoneticLeakError,
    render_slide,
)


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


def test_render_slide_with_png_alpha_asset(tmp_path: Path) -> None:
    """Test rendering slide with a static PNG with alpha channel (e.g. dripping_ice)."""
    asset_path = Path("custom_components/cast_studio/assets/dripping_ice.png").resolve()
    assert asset_path.exists(), "dripping_ice.png asset must exist for test"

    output_with_img = str(tmp_path / "slide_with_png.jpg")
    output_no_img = str(tmp_path / "slide_no_img.jpg")

    card_data_no_img = {
        "title": "Freezer Door Open!",
        "subtitle": "Kitchen Alert",
        "bullet_points": ["Duration: 60s"],
    }
    card_data_with_img = {
        **card_data_no_img,
        "image_path": str(asset_path),
    }

    res_no = render_slide(card_data_no_img, output_no_img)
    res_img = render_slide(card_data_with_img, output_with_img)

    assert os.path.exists(res_img)
    with Image.open(res_img) as img:
        assert img.size == (1024, 600)
        assert img.mode == "RGB"
        assert img.format == "JPEG"

    # Verify that the image was actually composited: pixel colors in icon region must differ
    with Image.open(res_no) as img_no, Image.open(res_img) as img_with:
        # Check an icon coordinate (e.g. x=200, y=250)
        pixel_no = img_no.getpixel((200, 250))
        pixel_with = img_with.getpixel((200, 250))
        assert pixel_no != pixel_with, (
            "Icon region pixel must differ when PNG is composited"
        )


def test_render_slide_with_static_jpeg_avatar(tmp_path: Path) -> None:
    """Test rendering slide with a static JPEG square avatar."""
    jpeg_file = tmp_path / "avatar.jpg"
    avatar_src = Image.new("RGB", (400, 400), (220, 60, 60))
    avatar_src.save(str(jpeg_file), "JPEG")

    output_path = str(tmp_path / "slide_jpeg_avatar.jpg")
    output_plain = str(tmp_path / "slide_plain.jpg")

    card_data = {
        "title": "Welcome Home, Javan!",
        "subtitle": "Arrived at 6:30 PM",
        "image_path": str(jpeg_file),
    }
    render_slide(
        {"title": "Welcome Home, Javan!", "subtitle": "Arrived at 6:30 PM"},
        output_plain,
    )
    result = render_slide(card_data, output_path)

    assert os.path.exists(result)
    with Image.open(result) as img:
        assert img.size == (1024, 600)
        assert img.mode == "RGB"
        assert img.format == "JPEG"

    with Image.open(output_plain) as plain_img, Image.open(result) as avatar_img:
        pixel_plain = plain_img.getpixel((200, 300))
        pixel_avatar = avatar_img.getpixel((200, 300))
        assert pixel_plain != pixel_avatar, (
            "Avatar region pixel must differ when JPEG avatar is composited"
        )


def test_render_slide_full_bleed_photo(tmp_path: Path) -> None:
    """Test rendering full-bleed photo visual with vignette overlay."""
    photo_file = tmp_path / "camera_feed.jpg"
    photo_src = Image.new("RGB", (1280, 720), (45, 120, 80))
    photo_src.save(str(photo_file), "JPEG")

    output_photo = str(tmp_path / "slide_full_bleed.jpg")
    output_default = str(tmp_path / "slide_default_grad.jpg")

    card_data = {
        "title": "Driveway Motion Detected",
        "subtitle": "Camera 1",
        "image_path": str(photo_file),
        "layout": "photo",
    }
    render_slide(
        {"title": "Driveway Motion Detected", "subtitle": "Camera 1"}, output_default
    )
    result = render_slide(card_data, output_photo)

    assert os.path.exists(result)
    with Image.open(result) as img:
        assert img.size == (1024, 600)
        assert img.mode == "RGB"
        assert img.format == "JPEG"

    # In full-bleed photo mode, the outer border/margin reflects the photo rather than the dark gradient
    with Image.open(output_default) as def_img, Image.open(result) as photo_img:
        margin_def = def_img.getpixel((20, 20))
        margin_photo = photo_img.getpixel((20, 20))
        assert margin_def != margin_photo, (
            "Outer margin must reflect photo background rather than default gradient"
        )


def test_render_slide_missing_image_path_fallback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that missing image_path falls back gracefully to standard typography card."""
    output_path = str(tmp_path / "slide_missing.jpg")
    card_data = {
        "title": "Alert With Missing Image",
        "image_path": "/path/to/nonexistent/missing_graphic.png",
    }
    with caplog.at_level("WARNING"):
        result = render_slide(card_data, output_path)

    assert os.path.exists(result)
    with Image.open(result) as img:
        assert img.size == (1024, 600)
        assert img.mode == "RGB"

    assert any("missing_graphic.png" in msg for msg in caplog.messages)


def test_render_slide_corrupt_image_fallback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that corrupted image file falls back gracefully to standard typography card."""
    corrupt_file = tmp_path / "corrupted.png"
    corrupt_file.write_bytes(b"not a real png header or data")

    output_path = str(tmp_path / "slide_corrupt.jpg")
    card_data = {
        "title": "Alert With Corrupt Image",
        "image_path": str(corrupt_file),
    }
    with caplog.at_level("WARNING"):
        result = render_slide(card_data, output_path)

    assert os.path.exists(result)
    with Image.open(result) as img:
        assert img.size == (1024, 600)

    assert any("corrupted.png" in msg for msg in caplog.messages)


def test_render_slide_identity_contract_with_image_present(tmp_path: Path) -> None:
    """Assert identity contract is enforced even when custom image is present."""
    asset_path = Path(
        "custom_components/cast_studio/assets/welcome_avatar.png"
    ).resolve()
    output_path = str(tmp_path / "slide_leak_with_image.jpg")
    card_data = {
        "title": "Welcome Home, Jay-ven!",
        "image_path": str(asset_path),
    }
    with pytest.raises(PhoneticLeakError, match="Jay-ven"):
        render_slide(card_data, output_path, phonetic_name="Jay-ven")

    assert not os.path.exists(output_path)


def test_render_slide_bundled_asset_lookup_by_name(tmp_path: Path) -> None:
    """Test resolving bundled asset when passed as icon name or icon key."""
    output_path = str(tmp_path / "slide_icon_name.jpg")
    output_plain = str(tmp_path / "slide_icon_plain.jpg")
    card_data = {
        "title": "Bundled Asset Lookup",
        "icon": "door_alert",
    }
    render_slide({"title": "Bundled Asset Lookup"}, output_plain)
    result = render_slide(card_data, output_path)
    assert os.path.exists(result)
    with Image.open(result) as img:
        assert img.size == (1024, 600)

    with Image.open(output_plain) as plain_img, Image.open(result) as icon_img:
        pixel_plain = plain_img.getpixel((200, 300))
        pixel_icon = icon_img.getpixel((200, 300))
        assert pixel_plain != pixel_icon, (
            "Icon pixel must differ when bundled asset is resolved"
        )
