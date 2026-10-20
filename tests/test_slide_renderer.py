"""Tests for Pillow Slide Visual Renderer (Task 5)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from custom_components.cast_studio.slide_renderer import (
    PhoneticLeakError,
    _draw_daily_encouragement_slide,
    _draw_google_nest_commute_slide,
    _draw_google_nest_weather_slide,
    _draw_two_column_agenda,
    _get_font,
    _layout_commute_chips,
    _load_image,
    _resolve_image_path,
    load_weather_icon,
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


def test_resolve_image_path_api_image_serve(tmp_path: Path) -> None:
    """Test resolving /api/image/serve/<id>/<size> URL to local storage file."""
    fake_storage = tmp_path / "image"
    fake_storage.mkdir()
    fake_img = fake_storage / "test_avatar_123"
    fake_img.write_text("avatar data")

    with patch(
        "custom_components.cast_studio.slide_renderer.Path.is_file",
        autospec=True,
    ) as mock_isfile:
        mock_isfile.side_effect = lambda self: "test_avatar_123" in str(self)
        resolved = _resolve_image_path("/api/image/serve/test_avatar_123/512x512")
        assert resolved is not None
        assert "test_avatar_123" in str(resolved)


def test_load_image_avatar_fallback_when_primary_missing() -> None:
    """Test _load_image falls back to avatar_fallback or bundled asset when primary image fails."""
    card_data = {
        "image_path": "/nonexistent/path/user_pic.png",
        "avatar_fallback": "welcome_avatar.png",
    }
    img, has_alpha = _load_image(card_data)
    assert img is not None
    assert has_alpha is True
    assert img.size == (512, 512)


def test_resolve_slide_theme_by_step_and_weather_condition() -> None:
    """Test resolve_slide_theme resolves step-specific and weather-aware palettes."""
    from custom_components.cast_studio.slide_renderer import (
        SLIDE_THEMES,
        resolve_slide_theme,
    )

    assert "greeting" in SLIDE_THEMES
    assert "national_day" in SLIDE_THEMES
    assert "fun_fact" in SLIDE_THEMES
    assert "agenda" in SLIDE_THEMES
    assert "commute" in SLIDE_THEMES
    assert "scripture" in SLIDE_THEMES

    rain_theme = resolve_slide_theme(
        {"step_name": "weather", "weather_condition": "rainy"}
    )
    sunny_theme = resolve_slide_theme(
        {"step_name": "weather", "weather_condition": "sunny"}
    )
    assert rain_theme["bg_top"] != sunny_theme["bg_top"]
    assert rain_theme["badge"] == "TODAY'S WEATHER"

    nd_theme = resolve_slide_theme({"step_name": "national_day"})
    assert nd_theme["badge"] == "NATIONAL DAY"

    agenda_theme = resolve_slide_theme({"step_name": "agenda"})
    assert agenda_theme["badge"] == "AGENDA"

    commute_theme = resolve_slide_theme({"step_name": "commute"})
    assert commute_theme["badge"] == "COMMUTE OVERVIEW"


def test_render_slide_applies_step_theme_and_category_badge(tmp_path: Path) -> None:
    """Test render_slide applies themed background gradient and category badge when step_name/theme is set."""
    default_out = str(tmp_path / "default_slide.jpg")
    themed_out = str(tmp_path / "themed_slide.jpg")

    render_slide({"title": "Morning Update"}, default_out)
    render_slide(
        {
            "title": "Morning Update",
            "step_name": "scripture",
            "badge": "DAILY SCRIPTURE",
        },
        themed_out,
    )

    with Image.open(default_out) as def_img, Image.open(themed_out) as thm_img:
        assert def_img.getpixel((10, 10)) != thm_img.getpixel((10, 10))


def test_render_slide_photo_mode_uses_translucent_frosted_glass_scrim(
    tmp_path: Path,
) -> None:
    """Test photo mode applies a frosted glass scrim leaving background photo vibrant and visible."""
    output_photo = str(tmp_path / "photo_scrim.jpg")
    card_data = {
        "title": "Sunny & Warm",
        "subtitle": "Weather Outlook",
        "image_path": "weather_sunny.jpg",
        "layout": "photo",
    }
    render_slide(card_data, output_photo)

    with Image.open(output_photo) as img:
        center_rgb = img.getpixel((500, 300))
        # Center should not be the dark opaque slate (R<55, G<60, B<70)
        # It must show the bright golden sunrise colors of weather_sunny.jpg (R >= 80, G >= 60)
        assert center_rgb[0] >= 80, (
            f"Expected vibrant red channel >= 80, got {center_rgb[0]}"
        )


def test_render_slide_two_column_agenda_layout(tmp_path: Path) -> None:
    """Test render_slide renders 2-column layout for combined calendar and to-do agenda."""
    output_path = str(tmp_path / "agenda_slide.jpg")
    card_data = {
        "title": "Today's Agenda",
        "subtitle": "Thursday, October 19 • 2 Events • 2 Tasks",
        "layout": "agenda_split",
        "calendar_items": [
            "09:00 AM — Architecture Planning",
            "01:30 PM — Design Review",
        ],
        "todo_items": [
            "Review CastStudio PR",
            "Water office plants",
        ],
        "image_path": "calendar_agenda.jpg",
    }
    result = render_slide(card_data, output_path)
    assert os.path.exists(result)
    with Image.open(result) as img:
        assert img.size == (1024, 600)
        assert img.mode == "RGB"


def test_render_slide_two_column_agenda_empty_states(tmp_path: Path) -> None:
    """Test render_slide handles empty calendar and to-do lists in 2-column layout."""
    output_path = str(tmp_path / "agenda_empty.jpg")
    card_data = {
        "title": "Today's Agenda",
        "subtitle": "Thursday, October 19 • Schedule Clear",
        "layout": "agenda_split",
        "calendar_items": [],
        "todo_items": [],
        "image_path": "calendar_agenda.jpg",
    }
    result = render_slide(card_data, output_path)
    assert os.path.exists(result)
    with Image.open(result) as img:
        assert img.size == (1024, 600)


def test_draw_header_suppresses_redundant_badge_matching_title(tmp_path: Path) -> None:
    """Test _draw_header suppresses redundant category pill when badge and title overlap."""
    out_redundant = str(tmp_path / "redundant_badge.jpg")
    card_data = {
        "title": "Good Morning, Javan",
        "subtitle": "Welcome to your day",
        "step_name": "greeting",
        # Theme has badge "GOOD MORNING"
    }
    result = render_slide(card_data, out_redundant)
    assert os.path.exists(result)


def test_render_slide_google_nest_weather_layout(tmp_path: Path) -> None:
    """Test render_slide renders 1024x600 Google Nest Hub weather layout with timeline."""
    output_png = str(tmp_path / "slide_weather.png")
    slide_data = {
        "step_name": "weather",
        "card": {
            "title": "Weather",
            "weather_data": {
                "current_temp": 68,
                "condition": "partlycloudy",
                "condition_text": "Mostly Sunny",
                "high": 74,
                "low": 54,
                "humidity": 48,
                "wind_speed": 8,
                "precipitation_probability": 10,
                "location": "Washington, D.C.",
                "forecast_points": [
                    {"time": "Now", "temperature": 68, "condition": "partlycloudy"},
                    {"time": "10 AM", "temperature": 70, "condition": "sunny"},
                    {"time": "1 PM", "temperature": 73, "condition": "sunny"},
                    {"time": "4 PM", "temperature": 74, "condition": "partlycloudy"},
                    {"time": "7 PM", "temperature": 67, "condition": "cloudy"},
                    {
                        "time": "Tomorrow",
                        "temperature": "72° / 52°",
                        "condition": "sunny",
                    },
                ],
            },
        },
    }
    render_slide(slide_data, output_png)
    assert Path(output_png).is_file()
    with Image.open(output_png) as img:
        assert img.size == (1024, 600)
        assert img.mode == "RGB"


def test_load_weather_icon_normalization_and_fallback() -> None:
    """Test load_weather_icon handles known condition aliases and unknown condition fallback."""
    # Known condition with alias
    img_partly = load_weather_icon("partlycloudy", size=(64, 64))
    assert img_partly.size == (64, 64)
    assert img_partly.mode == "RGBA"

    img_hyphen = load_weather_icon("partly-cloudy", size=(32, 32))
    assert img_hyphen.size == (32, 32)
    assert img_hyphen.mode == "RGBA"

    img_sunny = load_weather_icon("clear-night", size=(48, 48))
    assert img_sunny.size == (48, 48)

    # Unknown condition should safely fallback without raising
    fallback_img = load_weather_icon("apocalyptic_fog", size=(64, 64))
    assert fallback_img.size == (64, 64)
    assert fallback_img.mode == "RGBA"


def test_render_slide_google_nest_weather_missing_forecast_points(
    tmp_path: Path,
) -> None:
    """Test Google Nest weather slide renders cleanly without forecast points."""
    output_png = str(tmp_path / "slide_weather_no_forecast.png")
    slide_data = {
        "step_name": "weather",
        "card": {
            "title": "Weather",
            "weather_data": {
                "current_temp": 72,
                "condition": "sunny",
                "condition_text": "Sunny",
                "high": 75,
                "low": 55,
                "humidity": 40,
                "wind_speed": 5,
                "precipitation_probability": 0,
                "location": "Bethesda, MD",
                "forecast_points": [],
            },
        },
    }
    render_slide(slide_data, output_png)
    assert Path(output_png).is_file()
    with Image.open(output_png) as img:
        assert img.size == (1024, 600)


def test_render_slide_weather_fallback_without_weather_data(tmp_path: Path) -> None:
    """Test render_slide preserves legacy card rendering if step_name is weather but weather_data is absent."""
    output_jpg = str(tmp_path / "slide_weather_legacy.jpg")
    slide_data = {
        "step_name": "weather",
        "title": "Today's Weather",
        "subtitle": "Warm and sunny",
        "bullet_points": ["High of 75°", "Clear skies all day"],
    }
    result = render_slide(slide_data, output_jpg)
    assert Path(result).is_file()
    with Image.open(result) as img:
        assert img.size == (1024, 600)


def test_draw_google_nest_weather_slide_direct_call() -> None:
    """Test _draw_google_nest_weather_slide directly paints onto canvas with theme."""
    canvas = Image.new("RGB", (1024, 600))
    card = {
        "title": "Weather Briefing",
        "weather_data": {
            "current_temp": 65,
            "condition": "rainy",
            "condition_text": "Showers",
            "high": 68,
            "low": 50,
            "humidity": 85,
            "wind_speed": 12,
            "precipitation_probability": 90,
            "location": "Seattle, WA",
            "forecast_points": [
                {"time": "Now", "temperature": 65, "condition": "rainy"},
                {"time": "3 PM", "temperature": 67, "condition": "rainy"},
            ],
        },
    }
    _draw_google_nest_weather_slide(canvas, card, theme={"badge": "WEATHER"})
    assert canvas.size == (1024, 600)


def test_render_slide_dual_focus_agenda_layout(tmp_path: Path) -> None:
    output_png = str(tmp_path / "slide_agenda.png")
    slide_data = {
        "step_name": "agenda",
        "card": {
            "title": "Today's Schedule & Tasks",
            "agenda_data": {
                "date_str": "Monday, Oct 19",
                "calendar_events": [
                    {
                        "time": "10:00 AM – 11:00 AM",  # noqa: RUF001
                        "title": "Team Architecture Alignment",
                        "location": "Room A",
                    },
                    {
                        "time": "2:30 PM – 3:30 PM",  # noqa: RUF001
                        "title": "Weekly Project Review",
                        "location": "Google Meet",
                    },
                ],
                "todo_items": [
                    "50 push-ups & morning mobility",
                    "Prescription refill reminder",
                    "Review pull request",
                ],
            },
        },
    }
    render_slide(slide_data, output_png)
    assert Path(output_png).is_file()
    with Image.open(output_png) as img:
        assert img.size == (1024, 600)
        assert img.mode == "RGB"


def test_render_slide_dual_focus_agenda_empty_states(tmp_path: Path) -> None:
    output_png = str(tmp_path / "slide_agenda_empty.png")
    slide_data = {
        "step_name": "agenda",
        "card": {
            "title": "Today's Schedule & Tasks",
            "agenda_data": {
                "date_str": "Monday, Oct 19",
                "calendar_events": [],
                "todo_items": [],
            },
        },
    }
    render_slide(slide_data, output_png)
    assert Path(output_png).is_file()
    with Image.open(output_png) as img:
        assert img.size == (1024, 600)
        assert img.mode == "RGB"


def test_draw_two_column_agenda_direct_call() -> None:
    """Test _draw_two_column_agenda direct canvas rendering interface."""
    canvas = Image.new("RGB", (1024, 600))
    card = {
        "title": "Today's Schedule & Tasks",
        "agenda_data": {
            "date_str": "Monday, Oct 19",
            "calendar_events": [
                {
                    "time": "10:00 AM – 11:00 AM",  # noqa: RUF001
                    "title": "Team Architecture Alignment",
                    "location": "Room A",
                }
            ],
            "todo_items": ["Review pull request"],
        },
    }
    _draw_two_column_agenda(canvas, card, theme={"badge": "AGENDA"})
    assert canvas.size == (1024, 600)


def _commute_card(map_path: str | None, **overrides: object) -> dict:
    data = {
        "selected_mode": "driving",
        "drive_time": 35,
        "transit_time": 44,
        "diff_minutes": 9,
        "faster_mode": "driving",
        "drive_summary": "I-66 W",
        "transit_summary": "Silver Line",
        "summary": "via I-66 W",
        "distance": "24.5 mi",
        "eta": "8:45 AM",
        "traffic_condition": "typical",
        "map_image_path": map_path,
    }
    data.update(overrides)
    return {
        "step_name": "commute",
        "title": "Commute to Work",
        "commute_data": data,
    }


def test_render_slide_google_nest_commute_layout(tmp_path: Path) -> None:
    """Commute slide composites the live map and renders the dual-mode HUD."""
    map_file = tmp_path / "map.png"
    Image.new("RGB", (1024, 600), (250, 0, 0)).save(map_file)
    out = tmp_path / "commute.png"

    render_slide(_commute_card(str(map_file)), str(out))

    img = Image.open(out)
    assert img.size == (1024, 600)
    # Far right-middle is map-only territory: the red map shows through the vignette.
    r, g, b = img.convert("RGB").getpixel((990, 300))
    assert r > g + 60
    assert r > b + 60


def test_render_slide_google_nest_commute_chips_differ_by_selection(
    tmp_path: Path,
) -> None:
    """The selected mode chip is rendered differently from the unselected chip."""
    drive = tmp_path / "drive.png"
    transit = tmp_path / "transit.png"
    render_slide(_commute_card(None), str(drive))
    render_slide(
        _commute_card(None, selected_mode="transit", faster_mode="driving"),
        str(transit),
    )
    a = Image.open(drive).convert("RGB")
    b = Image.open(transit).convert("RGB")
    assert a.tobytes() != b.tobytes()


def test_render_slide_google_nest_commute_single_mode_has_no_second_chip(
    tmp_path: Path,
) -> None:
    """Without a transit time no comparison chip or FASTER badge is drawn."""
    both = tmp_path / "both.png"
    single = tmp_path / "single.png"
    render_slide(_commute_card(None), str(both))
    render_slide(
        _commute_card(None, transit_time=None, diff_minutes=None, transit_summary=""),
        str(single),
    )
    a = Image.open(both).convert("RGB")
    b = Image.open(single).convert("RGB")
    assert Image.open(single).size == (1024, 600)
    # Second chip region (right of the drive chip, bottom band) differs.
    assert (
        a.crop((330, 510, 700, 590)).tobytes() != b.crop((330, 510, 700, 590)).tobytes()
    )


def test_render_slide_google_nest_commute_missing_map_file_falls_back(
    tmp_path: Path,
) -> None:
    """A missing or unreadable map image falls back to the procedural dark map."""
    out = tmp_path / "fallback.png"
    render_slide(_commute_card(str(tmp_path / "does_not_exist.png")), str(out))
    assert Image.open(out).size == (1024, 600)


def test_draw_google_nest_commute_slide_direct_without_data() -> None:
    """Direct call with empty commute_data does not raise and paints the canvas."""
    canvas = Image.new("RGB", (1024, 600))
    _draw_google_nest_commute_slide(canvas, {"commute_data": {}})
    assert canvas.getpixel((990, 300)) != (0, 0, 0)


def _weather_card(advisory: str | None) -> dict:
    data = {
        "current_temp": 52,
        "condition": "rainy",
        "high": 60,
        "low": 48,
        "humidity": 85,
        "wind_speed": 12,
        "precipitation_probability": 90,
        "forecast_points": [{"time": "Now", "condition": "rainy", "temperature": 52}],
    }
    if advisory is not None:
        data["advisory"] = advisory
    return {"step_name": "weather", "title": "Weather", "weather_data": data}


def test_render_slide_weather_advisory_chip(tmp_path: Path) -> None:
    """Advisory chip is painted in the hero section."""
    with_adv = tmp_path / "adv.png"
    without = tmp_path / "none.png"
    render_slide(_weather_card("Pack an umbrella for rain today"), str(with_adv))
    render_slide(_weather_card(None), str(without))
    a = Image.open(with_adv).convert("RGB")
    b = Image.open(without).convert("RGB")
    assert a.size == (1024, 600)
    region = (260, 190, 860, 240)
    assert a.crop(region).tobytes() != b.crop(region).tobytes()


def test_render_slide_weather_long_advisory_fits(tmp_path: Path) -> None:
    """A very long advisory is truncated and never reaches the hero icon column."""
    long_text = "Pack an umbrella and waterproof boots for heavy rain today " * 6
    with_adv = tmp_path / "long.png"
    without = tmp_path / "none.png"
    render_slide(_weather_card(long_text), str(with_adv))
    render_slide(_weather_card(None), str(without))
    a = Image.open(with_adv).convert("RGB")
    b = Image.open(without).convert("RGB")
    assert a.size == (1024, 600)
    # Right of the chip limit (hero icon column and below) is untouched by the chip.
    region = (856, 190, 1024, 245)
    assert a.crop(region).tobytes() == b.crop(region).tobytes()


def _enc_card(**overrides: object) -> dict:
    card: dict = {
        "step_name": "daily_encouragement",
        "title": "Focus on what you can control, and take the day one step at a time.",
        "bullets": [
            "Keep your perspective: Don't let morning delays derail your peace.",
            "Prioritize with care: Focus your energy on the people and tasks that "
            "matter most today.",
        ],
        "host_signoff": "Have a great day, Javan!",
    }
    card.update(overrides)
    return card


def _render_enc(tmp_path: Path, name: str, card: dict) -> Image.Image:
    out = tmp_path / f"{name}.png"
    render_slide(card, str(out))
    img = Image.open(out).convert("RGB")
    assert img.size == (1024, 600)
    return img


def test_render_slide_daily_encouragement_layout(tmp_path: Path) -> None:
    """Two-bullet encouragement slide renders at canvas size."""
    _render_enc(tmp_path, "two", _enc_card())


def test_render_slide_daily_encouragement_bullet_points_key(tmp_path: Path) -> None:
    """The generator's bullet_points key renders the same as bullets."""
    a = _render_enc(tmp_path, "a", _enc_card())
    card = _enc_card()
    card["bullet_points"] = card.pop("bullets")
    b = _render_enc(tmp_path, "b", card)
    assert a.tobytes() == b.tobytes()


def test_render_slide_daily_encouragement_zero_and_one_bullet(tmp_path: Path) -> None:
    """0 and 1 bullets lay out cleanly and differ from the 2-bullet layout."""
    two = _render_enc(tmp_path, "two", _enc_card())
    one = _render_enc(
        tmp_path, "one", _enc_card(bullets=["Be kind: It costs nothing."])
    )
    zero = _render_enc(tmp_path, "zero", _enc_card(bullets=[]))
    assert two.tobytes() != one.tobytes() != zero.tobytes()


def test_render_slide_daily_encouragement_missing_signoff(tmp_path: Path) -> None:
    """A missing host_signoff renders safely (no sign-off text)."""
    card = _enc_card()
    del card["host_signoff"]
    with_sign = _render_enc(tmp_path, "s", _enc_card())
    without = _render_enc(tmp_path, "n", card)
    assert with_sign.crop((88, 480, 500, 540)).tobytes() != (
        without.crop((88, 480, 500, 540)).tobytes()
    )


def test_render_slide_daily_encouragement_overlong_text(tmp_path: Path) -> None:
    """Over-long headline, bullets and sign-off stay inside the card."""
    long_words = "extraordinarily " * 40
    card = _enc_card(
        title=long_words,
        bullets=[f"Lead words: {long_words}", long_words.replace(" ", "")],
        host_signoff="Have a wonderful day, " + long_words,
    )
    img = _render_enc(tmp_path, "long", card)
    ref = _render_enc(tmp_path, "ref", _enc_card())
    # Margins outside the card are identical to the plain render.
    for box in (
        (0, 0, 1024, 38),
        (0, 562, 1024, 600),
        (0, 0, 38, 600),
        (986, 0, 1024, 600),
    ):
        assert img.crop(box).tobytes() == ref.crop(box).tobytes()
    # Right padding inside the card stays clear of text.
    assert (
        img.crop((938, 90, 982, 550)).tobytes()
        == ref.crop((938, 90, 982, 550)).tobytes()
    )


def test_draw_daily_encouragement_slide_direct_empty() -> None:
    """Direct call with an empty card paints the canvas without raising."""
    canvas = Image.new("RGB", (1024, 600))
    _draw_daily_encouragement_slide(canvas, {}, {})
    assert canvas.getpixel((5, 5)) != (0, 0, 0)


LONG_DRIVE = "I-495 Express Lanes and Dulles Access Rd and then some more"
LONG_TRANSIT = "Silver Line to Ashburn via Wiehle-Reston East and Reston Town Center"


def _spy_texts(card: dict) -> list[tuple[int, str]]:
    """Render the commute HUD, recording every draw.text right edge and text."""
    edges: list[tuple[int, str]] = []
    orig = ImageDraw.ImageDraw.text

    def spy(
        draw_self: ImageDraw.ImageDraw, xy: Any, text: Any, *args: Any, **kwargs: Any
    ) -> Any:
        bbox = draw_self.textbbox(xy, text, font=kwargs.get("font"))
        edges.append((int(bbox[2]), str(text)))
        return orig(draw_self, xy, text, *args, **kwargs)

    canvas = Image.new("RGB", (1024, 600))
    with patch.object(ImageDraw.ImageDraw, "text", spy):
        _draw_google_nest_commute_slide(canvas, card)
    return edges


def test_commute_hud_long_labels_stay_inside_canvas() -> None:
    """Long route labels are ellipsized; no text or chip passes x=1024 (I2)."""
    card = _commute_card(
        None,
        drive_summary=LONG_DRIVE,
        transit_summary=LONG_TRANSIT,
        summary=f"via {LONG_DRIVE}",
        traffic_condition="heavy",
        distance="39.43211 kilometres of road",
        eta="10:45 AM",
    )
    edges = _spy_texts(card)
    assert edges
    assert all(right <= 1024 for right, _ in edges), edges
    texts = [t for _, t in edges]
    assert any(t.endswith("\u2026") for t in texts)
    # Hero ETA row stays inside the 350px hero card.
    eta_rows = [r for r, t in edges if t.startswith("Arrive around")]
    assert eta_rows
    assert all(r <= 380 for r in eta_rows)


def test_layout_commute_chips_caps_width_for_long_labels() -> None:
    """Pure chip layout keeps the right-most chip box within the canvas (I2)."""
    draw = ImageDraw.Draw(Image.new("RGB", (1024, 600)))
    font = _get_font(20, bold=True)
    boxes = _layout_commute_chips(
        draw, [("driving", 35, LONG_DRIVE), ("transit", 44, LONG_TRANSIT)], font
    )
    assert len(boxes) == 2
    assert boxes[-1][1] <= 1024 - 30
    assert boxes[0][1] < boxes[1][0]
    assert all(text.endswith("\u2026") for *_, text in boxes)


@pytest.mark.parametrize(
    ("overrides", "absent"),
    [
        (
            {
                "selected_mode": "transit",
                "faster_mode": "transit",
                "drive_time": None,
                "diff_minutes": None,
                "drive_summary": "Driving",
            },
            "Driving",
        ),
        (
            {"transit_time": None, "diff_minutes": None},
            "Silver Line",
        ),
    ],
    ids=["transit_only", "drive_only"],
)
def test_commute_hud_single_mode_draws_no_placeholder_chip(
    overrides: dict, absent: str
) -> None:
    """Single-mode renders draw no chip row and no '-- min' placeholder (I3)."""
    texts = [t for _, t in _spy_texts(_commute_card(None, **overrides))]
    assert not any("-- min" in t for t in texts)
    assert not any(" min \u2022 " in t for t in texts)
    assert not any(absent in t for t in texts)


def test_commute_hud_both_unavailable_has_no_false_eta() -> None:
    """With no travel times there is no arrival line and no chips (I3)."""
    card = _commute_card(
        None,
        drive_time=None,
        transit_time=None,
        diff_minutes=None,
        eta="",
        distance="",
    )
    texts = [t for _, t in _spy_texts(card)]
    assert not any("Arrive around" in t for t in texts)
    assert not any("0 min" in t for t in texts)
    assert not any("FASTER" in t for t in texts)


def test_commute_hud_relative_eta_has_no_clock_prefix() -> None:
    """A pregenerated (relative) ETA is shown as the trip length (I4)."""
    card = _commute_card(None, eta="35 min trip", eta_is_relative=True)
    texts = [t for _, t in _spy_texts(card)]
    assert any(t.startswith("35 min trip") for t in texts)
    assert not any("Arrive around" in t for t in texts)


def test_commute_hud_tie_has_no_faster_badge() -> None:
    """A zero-minute difference does not claim one mode is faster."""
    card = _commute_card(None, transit_time=35, diff_minutes=0)
    texts = [t for _, t in _spy_texts(card)]
    assert not any("FASTER" in t for t in texts)
    assert not any(t.startswith("Fastest route") for t in texts)
