"""
Pillow Slide Visual Renderer (Real Name Enforced).

Renders high-contrast 1024x600 pixels (16:9 aspect ratio) RGB JPEG slides
for smart displays with dark gradients, translucent rounded cards, and typography.
Enforces the identity contract by asserting that phonetic spellings never leak onto
visual slides.
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

from homeassistant.util import dt as dt_util
from PIL import Image, ImageDraw, ImageFont

_LOGGER = logging.getLogger(__name__)
BUNDLED_ASSETS_DIR = Path(__file__).parent / "assets"
WEATHER_ASSETS_DIR = BUNDLED_ASSETS_DIR / "weather"

SlideTheme = dict[str, Any]
MID_GRADIENT_STOP = 0.5

WEATHER_ICON_MAP: dict[str, str] = {
    "sunny": "sunny.png",
    "clear": "sunny.png",
    "clearnight": "sunny.png",
    "sunnynight": "sunny.png",
    "fair": "sunny.png",
    "partlycloudy": "partly_cloudy.png",
    "partlysunny": "partly_cloudy.png",
    "mostlysunny": "partly_cloudy.png",
    "scatteredclouds": "partly_cloudy.png",
    "cloudy": "cloudy.png",
    "overcast": "cloudy.png",
    "mostlycloudy": "cloudy.png",
    "fog": "cloudy.png",
    "mist": "cloudy.png",
    "haze": "cloudy.png",
    "smoke": "cloudy.png",
    "brokenclouds": "cloudy.png",
    "rainy": "rainy.png",
    "rain": "rainy.png",
    "pouring": "rainy.png",
    "drizzle": "rainy.png",
    "showers": "rainy.png",
    "shower": "rainy.png",
    "lightrain": "rainy.png",
    "heavyrain": "rainy.png",
    "stormy": "stormy.png",
    "lightning": "stormy.png",
    "thunderstorm": "stormy.png",
    "storm": "stormy.png",
    "lightningrainy": "stormy.png",
    "thunder": "stormy.png",
    "snowy": "snowy.png",
    "snow": "snowy.png",
    "snowyrainy": "snowy.png",
    "sleet": "snowy.png",
    "hail": "snowy.png",
    "blizzard": "snowy.png",
    "flurries": "snowy.png",
    "lightsnow": "snowy.png",
    "heavysnow": "snowy.png",
    "ice": "snowy.png",
    "humidity": "humidity.png",
    "humid": "humidity.png",
    "wind": "wind.png",
    "windy": "wind.png",
    "breezy": "wind.png",
    "windyvariant": "wind.png",
    "precip": "precip.png",
    "precipitation": "precip.png",
    "umbrella": "precip.png",
}

# Display dimensions
CANVAS_WIDTH = 1024
CANVAS_HEIGHT = 600

# Color palette (high-contrast dark theme defaults)
BG_COLOR_TOP = (15, 23, 42)  # Slate-900
BG_COLOR_BOTTOM = (2, 6, 23)  # Slate-950
CARD_BG_COLOR = (30, 41, 59, 215)  # Slate-800 with ~84% opacity
CARD_BORDER_COLOR = (71, 85, 105, 200)  # Slate-600
TITLE_COLOR = (255, 255, 255)  # Crisp white
SUBTITLE_COLOR = (56, 189, 248)  # Sky-400 accent
DIVIDER_COLOR = (51, 65, 85, 180)  # Slate-700
BULLET_TEXT_COLOR = (241, 245, 249)  # Slate-100
BULLET_DOT_COLOR = (56, 189, 248)  # Sky-400 accent
MIN_BADGE_WORD_LEN = 3


SLIDE_THEMES: dict[str, dict[str, Any]] = {
    "default": {
        "bg_top": BG_COLOR_TOP,
        "bg_bottom": BG_COLOR_BOTTOM,
        "accent_color": SUBTITLE_COLOR,
        "card_border": CARD_BORDER_COLOR,
        "badge": "",
    },
    "greeting": {
        "bg_top": (32, 18, 10),
        "bg_bottom": (95, 48, 15),
        "accent_color": (255, 215, 120),
        "card_border": (180, 120, 60, 200),
        "badge": "GOOD MORNING",
    },
    "weather": {
        "bg_top": (28, 20, 12),
        "bg_bottom": (110, 62, 18),
        "accent_color": (253, 224, 71),
        "card_border": (160, 110, 50, 200),
        "badge": "TODAY'S WEATHER",
    },
    "weather_sunny": {
        "bg_top": (28, 20, 12),
        "bg_bottom": (110, 62, 18),
        "accent_color": (253, 224, 71),
        "card_border": (180, 130, 60, 200),
        "badge": "TODAY'S WEATHER",
    },
    "weather_cloudy": {
        "bg_top": (24, 30, 38),
        "bg_bottom": (55, 68, 82),
        "accent_color": (130, 210, 255),
        "card_border": (100, 116, 139, 200),
        "badge": "TODAY'S WEATHER",
    },
    "weather_rainy": {
        "bg_top": (10, 16, 30),
        "bg_bottom": (20, 42, 64),
        "accent_color": (103, 232, 249),
        "card_border": (56, 189, 248, 180),
        "badge": "TODAY'S WEATHER",
    },
    "weather_snow": {
        "bg_top": (14, 28, 44),
        "bg_bottom": (35, 75, 105),
        "accent_color": (195, 235, 255),
        "card_border": (148, 210, 245, 200),
        "badge": "TODAY'S WEATHER",
    },
    "national_day": {
        "bg_top": (25, 12, 8),
        "bg_bottom": (70, 30, 15),
        "accent_color": (255, 170, 80),
        "card_border": (200, 100, 40, 200),
        "badge": "NATIONAL DAY",
    },
    "fun_fact": {
        "bg_top": (15, 8, 35),
        "bg_bottom": (42, 18, 75),
        "accent_color": (255, 180, 100),
        "card_border": (126, 87, 194, 200),
        "badge": "FUN FACT",
    },
    "agenda": {
        "bg_top": (8, 22, 16),
        "bg_bottom": (18, 48, 34),
        "accent_color": (110, 231, 183),
        "card_border": (52, 140, 100, 200),
        "badge": "AGENDA",
    },
    "calendar": {
        "bg_top": (8, 22, 16),
        "bg_bottom": (18, 48, 34),
        "accent_color": (110, 231, 183),
        "card_border": (52, 140, 100, 200),
        "badge": "TODAY'S SCHEDULE",
    },
    "todo": {
        "bg_top": (8, 22, 16),
        "bg_bottom": (18, 48, 34),
        "accent_color": (110, 231, 183),
        "card_border": (52, 140, 100, 200),
        "badge": "PRIORITY TASKS",
    },
    "commute": {
        "bg_top": (16, 19, 26),
        "bg_bottom": (32, 38, 48),
        "accent_color": (251, 191, 36),
        "card_border": (180, 135, 40, 200),
        "badge": "COMMUTE OVERVIEW",
    },
    "scripture": {
        "bg_top": (26, 14, 9),
        "bg_bottom": (52, 28, 16),
        "accent_color": (250, 220, 160),
        "card_border": (160, 115, 70, 200),
        "badge": "DAILY SCRIPTURE",
    },
    "bedtime": {
        "bg_top": (10, 12, 28),
        "bg_bottom": (24, 20, 52),
        "accent_color": (196, 181, 253),
        "card_border": (109, 93, 175, 200),
        "badge": "EVENING WIND-DOWN",
    },
    "wind_down": {
        "bg_top": (10, 12, 28),
        "bg_bottom": (24, 20, 52),
        "accent_color": (196, 181, 253),
        "card_border": (109, 93, 175, 200),
        "badge": "EVENING WIND-DOWN",
    },
    "alert": {
        "bg_top": (40, 12, 16),
        "bg_bottom": (24, 8, 12),
        "accent_color": (248, 113, 113),
        "card_border": (220, 68, 68, 210),
        "badge": "ALERT",
    },
    "welcome": {
        "bg_top": (10, 26, 28),
        "bg_bottom": (16, 52, 56),
        "accent_color": (94, 234, 212),
        "card_border": (45, 160, 150, 200),
        "badge": "WELCOME HOME",
    },
}


def resolve_slide_theme(card_data: dict[str, Any]) -> dict[str, Any]:
    """Resolve visual theme palette and category badge from card_data."""
    explicit_theme = str(card_data.get("theme") or "").strip().lower()
    step_name = (
        str(card_data.get("step_name") or card_data.get("type") or "").strip().lower()
    )
    condition = str(card_data.get("weather_condition") or "").strip().lower()

    theme_key = "default"
    if explicit_theme and explicit_theme in SLIDE_THEMES:
        theme_key = explicit_theme
    elif step_name == "weather" or explicit_theme == "weather":
        rain_keywords = ("rain", "pour", "drizzle", "lightning", "storm")
        snow_keywords = ("snow", "sleet", "hail", "ice", "blizzard")
        cloud_keywords = ("cloud", "overcast", "fog", "mist", "wind")
        if any(w in condition for w in rain_keywords):
            theme_key = "weather_rainy"
        elif any(w in condition for w in snow_keywords):
            theme_key = "weather_snow"
        elif any(w in condition for w in cloud_keywords):
            theme_key = "weather_cloudy"
        else:
            theme_key = "weather_sunny"
    elif step_name in SLIDE_THEMES:
        theme_key = step_name

    resolved = dict(SLIDE_THEMES.get(theme_key, SLIDE_THEMES["default"]))
    if "badge" in card_data and card_data["badge"] is not None:
        resolved["badge"] = str(card_data["badge"]).strip()
    return resolved


# Card bounding layout
CARD_X0 = 60
CARD_Y0 = 45
CARD_X1 = 964
CARD_Y1 = 555
CARD_RADIUS = 24

CONTENT_LEFT = CARD_X0 + 44
CONTENT_RIGHT = CARD_X1 - 44

# Side-by-side icon layout dimensions
ICON_BOX_X = CARD_X0 + 44
ICON_BOX_MAX_W = 320
ICON_BOX_MAX_H = 320
ICON_CENTER_Y = (CARD_Y0 + CARD_Y1) // 2

ASPECT_RATIO_WIDESCREEN_THRESHOLD = 1.3
OPAQUE_ALPHA = 255


class PhoneticLeakError(ValueError, AssertionError):
    """Exception raised when a phonetic pronunciation leaks onto a visual slide."""


class InvalidCardDataError(ValueError, TypeError):
    """Exception raised when card_data is not a valid dictionary."""


def _get_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    """Resolve an appropriate TrueType font or fallback to load_default."""
    candidates = (
        [
            "DejaVuSans-Bold.ttf",
            "HelveticaBold.ttc",
            "Arial-Bold.ttf",
            "FreeSansBold.ttf",
            "LiberationSans-Bold.ttf",
            "Helvetica.ttc",
            "DejaVuSans.ttf",
            "Arial.ttf",
        ]
        if bold
        else [
            "DejaVuSans.ttf",
            "Helvetica.ttc",
            "Arial.ttf",
            "FreeSans.ttf",
            "LiberationSans-Regular.ttf",
        ]
    )

    for font_name in candidates:
        try:
            return ImageFont.truetype(font_name, size)
        except OSError:
            continue

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrap_text(
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    """Wrap words into lines fitting within max_width."""
    words = text.split(" ")
    lines: list[str] = []
    current_line: list[str] = []

    for word in words:
        candidate = " ".join([*current_line, word])
        bbox = draw.textbbox((0, 0), candidate, font=font)
        line_w = bbox[2] - bbox[0]
        if line_w <= max_width or not current_line:
            current_line.append(word)
        else:
            lines.append(" ".join(current_line))
            current_line = [word]

    if current_line:
        lines.append(" ".join(current_line))

    return lines or [text]


def _contains_word(text: str, target: str) -> bool:
    """Check if target word appears in text with word boundaries."""
    word = target.strip()
    if not word:
        return False
    prefix = r"\b" if re.match(r"^\w", word) else ""
    suffix = r"\b" if re.search(r"\w$", word) else ""
    pattern = re.compile(rf"{prefix}{re.escape(word)}{suffix}", re.IGNORECASE)
    return bool(pattern.search(text))


def _extract_disallowed_words(
    card_data: dict[str, Any],
    phonetic_name: str | None,
) -> set[str]:
    """Extract all phonetic or disallowed words to screen against."""
    disallowed: set[str] = set()
    if phonetic_name:
        disallowed.add(phonetic_name.strip())
    if card_data.get("phonetic_name"):
        disallowed.add(str(card_data["phonetic_name"]).strip())

    recipient = card_data.get("recipient")
    if isinstance(recipient, dict) and recipient.get("phonetic_name"):
        disallowed.add(str(recipient["phonetic_name"]).strip())

    disallowed_words = card_data.get("disallowed_words")
    if isinstance(disallowed_words, list):
        for w in disallowed_words:
            disallowed.add(str(w).strip())
    elif isinstance(disallowed_words, str):
        disallowed.add(disallowed_words.strip())

    return disallowed


def _extract_allowed_words(card_data: dict[str, Any]) -> set[str]:
    """Extract permitted real names to prevent false positive leak detections."""
    allowed: set[str] = set()
    if card_data.get("name"):
        allowed.add(str(card_data["name"]).strip().lower())
    if card_data.get("real_name"):
        allowed.add(str(card_data["real_name"]).strip().lower())

    recipient = card_data.get("recipient")
    if isinstance(recipient, dict) and recipient.get("name"):
        allowed.add(str(recipient["name"]).strip().lower())

    return allowed


def _assert_identity_contract(
    card_data: dict[str, Any],
    phonetic_name: str | None = None,
) -> None:
    """Assert that phonetic pronunciation spellings never leak into visual card text."""
    disallowed = _extract_disallowed_words(card_data, phonetic_name)
    allowed = _extract_allowed_words(card_data)

    text_items: list[str] = []
    if title := card_data.get("title"):
        text_items.append(str(title))
    if subtitle := card_data.get("subtitle"):
        text_items.append(str(subtitle))

    raw_bullets = card_data.get("bullet_points")
    if isinstance(raw_bullets, list):
        text_items.extend(str(bp) for bp in raw_bullets)

    for candidate in disallowed:
        if not candidate or candidate.lower() in allowed:
            continue

        for text in text_items:
            if _contains_word(text, candidate):
                msg = (
                    f"Identity contract violation: Phonetic spelling '{candidate}' "
                    f"detected in visual card text: '{text}'"
                )
                raise PhoneticLeakError(msg)


def _render_gradient_background(
    theme: dict[str, Any] | None = None,
) -> Image.Image:
    """Render background canvas with vertical atmospheric gradient."""
    top_col = (theme or {}).get("bg_top", BG_COLOR_TOP)
    bot_col = (theme or {}).get("bg_bottom", BG_COLOR_BOTTOM)
    canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT))
    grad_draw = ImageDraw.Draw(canvas)
    for y in range(CANVAS_HEIGHT):
        ratio = y / CANVAS_HEIGHT
        r = int(top_col[0] * (1.0 - ratio) + bot_col[0] * ratio)
        g = int(top_col[1] * (1.0 - ratio) + bot_col[1] * ratio)
        b = int(top_col[2] * (1.0 - ratio) + bot_col[2] * ratio)
        grad_draw.line([(0, y), (CANVAS_WIDTH, y)], fill=(r, g, b))
    return canvas


def _render_card_overlay(
    canvas: Image.Image,
    theme: dict[str, Any] | None = None,
    *,
    is_photo: bool = False,
) -> Image.Image:
    """Composite translucent rounded card overlay on top of canvas."""
    overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    if is_photo:
        fill_col = (10, 16, 30, 100)
        border_col = (255, 255, 255, 50)
    else:
        fill_col = CARD_BG_COLOR
        border_col = (theme or {}).get("card_border", CARD_BORDER_COLOR)

    overlay_draw.rounded_rectangle(
        (CARD_X0, CARD_Y0, CARD_X1, CARD_Y1),
        radius=CARD_RADIUS,
        fill=fill_col,
        outline=border_col,
        width=1,
    )
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def load_weather_icon(condition: str, size: tuple[int, int] = (64, 64)) -> Image.Image:
    """Normalize weather condition string and load transparent weather icon asset."""
    clean = (
        str(condition or "")
        .strip()
        .lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
    )

    if clean in WEATHER_ICON_MAP:
        filename = WEATHER_ICON_MAP[clean]
    elif any(w in clean for w in ("rain", "pour", "drizzle", "shower")):
        filename = "rainy.png"
    elif any(w in clean for w in ("storm", "thunder", "lightning")):
        filename = "stormy.png"
    elif any(w in clean for w in ("snow", "sleet", "hail", "ice", "blizzard")):
        filename = "snowy.png"
    elif any(w in clean for w in ("cloud", "overcast", "fog", "mist", "haze")):
        filename = "cloudy.png"
    elif any(w in clean for w in ("sun", "clear")):
        filename = "sunny.png"
    elif "wind" in clean:
        filename = "wind.png"
    else:
        filename = "partly_cloudy.png"

    target_file = WEATHER_ASSETS_DIR / filename
    if target_file.is_file():
        try:
            with Image.open(target_file) as img:
                return img.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
        except (OSError, ValueError) as err:
            _LOGGER.warning("Failed to open weather icon '%s': %s", target_file, err)

    # Safe fallback if asset missing or corrupted
    fallback = Image.new("RGBA", size, (0, 0, 0, 0))
    fb_draw = ImageDraw.Draw(fallback)
    fb_draw.ellipse(
        [(2, 2), (size[0] - 2, size[1] - 2)],
        fill=(251, 191, 36, 230),
        outline=(245, 158, 11, 255),
        width=max(1, size[0] // 16),
    )
    return fallback


def _render_atmospheric_weather_gradient(condition: str) -> Image.Image:
    """Render condition-aware 135° atmospheric linear gradient base canvas."""
    cond = str(condition or "").strip().lower()
    if any(w in cond for w in ("rain", "pour", "drizzle", "storm", "lightning")):
        c0 = (30, 41, 59)
        c1 = (41, 53, 72)
        c2 = (51, 65, 85)
    elif any(w in cond for w in ("cloud", "overcast", "fog", "mist", "haze")):
        c0 = (30, 41, 59)
        c1 = (17, 73, 110)
        c2 = (3, 105, 161)
    elif any(w in cond for w in ("snow", "sleet", "ice", "blizzard", "hail")):
        c0 = (30, 41, 59)
        c1 = (16, 82, 129)
        c2 = (2, 132, 199)
    else:
        # Sunny/clear: #1e3a8a 0%, #0369a1 50%, #0284c7 100%
        c0 = (30, 58, 138)
        c1 = (3, 105, 161)
        c2 = (2, 132, 199)

    gw, gh = 128, 75
    pixels = bytearray(gw * gh * 3)
    idx = 0
    for y in range(gh):
        ry = y / (gh - 1)
        for x in range(gw):
            rx = x / (gw - 1)
            t = (rx + ry) / 2.0
            if t <= MID_GRADIENT_STOP:
                st = t / MID_GRADIENT_STOP
                r = int(c0[0] * (1.0 - st) + c1[0] * st)
                g = int(c0[1] * (1.0 - st) + c1[1] * st)
                b = int(c0[2] * (1.0 - st) + c1[2] * st)
            else:
                st = (t - MID_GRADIENT_STOP) / MID_GRADIENT_STOP
                r = int(c1[0] * (1.0 - st) + c2[0] * st)
                g = int(c1[1] * (1.0 - st) + c2[1] * st)
                b = int(c1[2] * (1.0 - st) + c2[2] * st)
            pixels[idx] = r
            pixels[idx + 1] = g
            pixels[idx + 2] = b
            idx += 3

    small = Image.frombytes("RGB", (gw, gh), bytes(pixels))
    return small.resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.Resampling.BILINEAR)


ADVISORY_CHIP_Y = 192
ADVISORY_CHIP_H = 40
ADVISORY_RIGHT_LIMIT = 850
ICON_PILL_PAD = 14

# Encouragement slide palette and geometry
ENC_CARD = (40, 40, 984, 560)
ENC_CARD_RADIUS = 18
ENC_PAD_X = 48
ENC_FOOTER_H = 84
ENC_CAPTION = "CastStudio Morning Host"
ENC_PILL_LABEL = "TODAY'S TAKEAWAY"
ENC_BULLET_COLORS = ((56, 189, 248), (52, 211, 153))  # Sky-400 / Emerald-400
ENC_MAX_BULLETS = 2
ENC_MAX_HEAD_LINES = 2
ENC_MAX_BULLET_LINES = 2
ELLIPSIS = "\u2026"


def _composite_overlay(canvas: Image.Image, paint: Any) -> ImageDraw.ImageDraw:
    """Paint translucent shapes via ``paint(draw)`` on a layer and alpha-composite."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    paint(ImageDraw.Draw(layer))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB"))
    return ImageDraw.Draw(canvas)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    """Return the rendered pixel width of text."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[2] - bbox[0])


def _truncate_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    """Trim text and append an ellipsis until it fits max_width."""
    if _text_width(draw, text, font) <= max_width:
        return text
    trimmed = text.rstrip()
    while trimmed and _text_width(draw, trimmed + ELLIPSIS, font) > max_width:
        trimmed = trimmed[:-1].rstrip()
    return trimmed + ELLIPSIS


def _fit_wrapped(  # noqa: PLR0913
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_lines: int,
    sizes: tuple[int, ...],
    *,
    bold: bool,
) -> tuple[list[str], ImageFont.ImageFont]:
    """Wrap text to max_lines, shrinking through sizes; ellipsize as last resort."""
    font = _get_font(sizes[-1], bold=bold)
    for size in sizes:
        font = _get_font(size, bold=bold)
        lines = _wrap_text(text, font, max_width, draw)
        if len(lines) <= max_lines and all(
            _text_width(draw, ln, font) <= max_width for ln in lines
        ):
            return lines, font
    lines = _wrap_text(text, font, max_width, draw)
    kept = lines[:max_lines]
    if len(lines) > max_lines:
        kept[-1] = " ".join([kept[-1], *lines[max_lines:]])
    return [_truncate_to_width(draw, ln, font, max_width) for ln in kept], font


def _draw_sun_icon(
    draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, fill: Any
) -> None:
    """Draw a simple vector sun (the default fonts have no emoji glyphs)."""
    core = max(2, r // 2)
    draw.ellipse((cx - core, cy - core, cx + core, cy + core), fill=fill)
    for i in range(8):
        ang = i * math.pi / 4
        x0 = cx + math.cos(ang) * (core + 2)
        y0 = cy + math.sin(ang) * (core + 2)
        x1 = cx + math.cos(ang) * r
        y1 = cy + math.sin(ang) * r
        draw.line((x0, y0, x1, y1), fill=fill, width=2)


def _draw_umbrella_icon(
    draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, fill: Any
) -> None:
    """Draw a simple vector umbrella."""
    draw.pieslice((cx - r, cy - r, cx + r, cy + r), 180, 360, fill=fill)
    draw.line((cx, cy, cx, cy + r - 2), fill=fill, width=2)
    draw.arc((cx - 4, cy + r - 8, cx, cy + r - 1), 0, 180, fill=fill, width=2)


def _draw_advisory_chip(
    canvas: Image.Image,
    text: str,
    *,
    x: int,
    y: int,
    max_w: int,
) -> None:
    """Draw the weather preparation advisory pill, shrinking/truncating to fit."""
    probe = ImageDraw.Draw(canvas)
    icon_w = 22
    pad = ICON_PILL_PAD
    text_max = max(40, max_w - icon_w - pad * 3)
    font = _get_font(14, bold=True)
    for size in (18, 16, 15, 14, 13):
        font = _get_font(size, bold=True)
        if _text_width(probe, text, font) <= text_max:
            break
    label = _truncate_to_width(probe, text, font, text_max)
    chip_w = _text_width(probe, label, font) + icon_w + pad * 3
    h = ADVISORY_CHIP_H

    def paint(d: ImageDraw.ImageDraw) -> None:
        d.rounded_rectangle(
            (x, y, x + chip_w, y + h),
            radius=h // 2,
            fill=(2, 132, 199, 90),
            outline=(56, 189, 248, 255),
            width=1,
        )

    draw = _composite_overlay(canvas, paint)
    _draw_umbrella_icon(
        draw, x + pad + icon_w // 2, y + h // 2 - 2, 10, (255, 255, 255)
    )
    bbox = draw.textbbox((0, 0), label, font=font)
    draw.text(
        (x + pad * 2 + icon_w, y + (h - (bbox[3] - bbox[1])) // 2 - bbox[1]),
        label,
        font=font,
        fill=(255, 255, 255),
    )


def _render_dawn_gradient() -> Image.Image:
    """Render a local procedural dawn gradient (no generated imagery)."""
    stops = ((30, 27, 75), (131, 58, 120), (234, 120, 90), (251, 191, 110))
    grad = Image.new("RGB", (1, CANVAS_HEIGHT))
    for y in range(CANVAS_HEIGHT):
        t = (y / (CANVAS_HEIGHT - 1)) * (len(stops) - 1)
        i = min(int(t), len(stops) - 2)
        f = t - i
        grad.putpixel(
            (0, y),
            tuple(int(stops[i][c] * (1 - f) + stops[i + 1][c] * f) for c in range(3)),
        )
    return grad.resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.Resampling.BILINEAR)


def _split_lead(text: str) -> tuple[str, str]:
    """Split '<Bold lead>: <sentence>' into (lead, rest); lead is '' if absent."""
    lead, sep, rest = text.partition(":")
    if sep and rest.strip() and 0 < len(lead.strip()) <= 40:  # noqa: PLR2004
        return lead.strip() + ":", rest.strip()
    return "", text.strip()


def _wrap_rich(
    draw: ImageDraw.ImageDraw,
    lead: str,
    rest: str,
    size: int,
    max_width: int,
) -> list[list[tuple[str, bool]]]:
    """Wrap words (bold lead + regular rest) into lines of (word, is_bold)."""
    fonts = {True: _get_font(size, bold=True), False: _get_font(size)}
    words = [(w, True) for w in lead.split()] + [(w, False) for w in rest.split()]
    lines: list[list[tuple[str, bool]]] = []
    cur: list[tuple[str, bool]] = []
    cur_w = 0
    space = _text_width(draw, "a a", fonts[False]) - _text_width(
        draw, "aa", fonts[False]
    )
    for word, bold in words:
        w = _text_width(draw, word, fonts[bold])
        add = w if not cur else space + w
        if cur and cur_w + add > max_width:
            lines.append(cur)
            cur, cur_w = [(word, bold)], w
        else:
            cur.append((word, bold))
            cur_w += add
    if cur:
        lines.append(cur)
    return lines or [[]]


def _line_width(
    draw: ImageDraw.ImageDraw, line: list[tuple[str, bool]], size: int
) -> int:
    """Return pixel width of a rich line."""
    total = 0
    for i, (word, bold) in enumerate(line):
        total += _text_width(draw, word, _get_font(size, bold=bold))
        if i:
            total += max(3, size // 4)
    return total


def _fit_rich(
    draw: ImageDraw.ImageDraw, text: str, max_width: int
) -> tuple[list[list[tuple[str, bool]]], int]:
    """Fit a bullet into <=2 lines by shrinking font; ellipsize as last resort."""
    lead, rest = _split_lead(text)
    lines: list[list[tuple[str, bool]]] = [[]]
    size = 16
    for size in (20, 19, 18, 17, 16):
        lines = _wrap_rich(draw, lead, rest, size, max_width)
        if len(lines) <= ENC_MAX_BULLET_LINES and all(
            _line_width(draw, ln, size) <= max_width for ln in lines
        ):
            return lines, size
    lines = _wrap_rich(draw, lead, rest, size, max_width)
    kept = lines[:ENC_MAX_BULLET_LINES]
    if len(lines) > ENC_MAX_BULLET_LINES:
        kept[-1] = [w for ln in lines[ENC_MAX_BULLET_LINES - 1 :] for w in ln]
    last = kept[-1]
    while (
        last
        and _line_width(draw, [*last[:-1], (last[-1][0] + ELLIPSIS, last[-1][1])], size)
        > max_width
    ):
        if len(last) == 1:
            word, bold = last[0]
            last[0] = (word[:-1], bold)
            if not word[:-1]:
                break
        else:
            last.pop()
    if last:
        last[-1] = (last[-1][0].rstrip() + ELLIPSIS, last[-1][1])
    return kept, size


def _draw_frosted_takeaway_card(  # noqa: PLR0913, PLR0915
    canvas: Image.Image,
    *,
    pill_label: str,
    headline: str,
    bullets: list[str],
    signoff: str,
    caption: str,
    pill_fill: tuple[int, int, int, int],
    pill_border: tuple[int, int, int],
    pill_text: tuple[int, int, int],
    signoff_color: tuple[int, int, int],
    bullet_colors: tuple[tuple[int, int, int], ...] = ENC_BULLET_COLORS,
    draw_pill_icon: Any = _draw_sun_icon,
) -> None:
    """Draw a reusable frosted card: pill, headline, bullets, divider, footer."""
    x0, y0, x1, y1 = ENC_CARD
    cx0, cx1 = x0 + ENC_PAD_X, x1 - ENC_PAD_X
    text_w = cx1 - cx0

    draw = _composite_overlay(
        canvas,
        lambda d: d.rounded_rectangle(
            ENC_CARD,
            radius=ENC_CARD_RADIUS,
            fill=(15, 23, 42, 191),
            outline=(255, 255, 255, 46),
            width=1,
        ),
    )

    # Label pill anchored at the top of the card
    pill_font = _get_font(13, bold=True)
    label = _truncate_to_width(draw, pill_label, pill_font, text_w - 60)
    pill_h = 32
    icon_w = 20
    pill_w = _text_width(draw, label, pill_font) + icon_w + 40
    pill_y = y0 + 30

    def paint_pill(d: ImageDraw.ImageDraw) -> None:
        d.rounded_rectangle(
            (cx0, pill_y, cx0 + pill_w, pill_y + pill_h),
            radius=pill_h // 2,
            fill=pill_fill,
            outline=(*pill_border, 255),
            width=1,
        )

    draw = _composite_overlay(canvas, paint_pill)
    draw_pill_icon(
        draw, cx0 + 18 + icon_w // 2 - 4, pill_y + pill_h // 2, 8, pill_border
    )
    pb = draw.textbbox((0, 0), label, font=pill_font)
    draw.text(
        (cx0 + 18 + icon_w + 4, pill_y + (pill_h - (pb[3] - pb[1])) // 2 - pb[1]),
        label,
        font=pill_font,
        fill=pill_text,
    )

    # Measure content (headline + bullets) to balance it in the body area.
    bullets = bullets[:ENC_MAX_BULLETS]
    head_sizes = (34, 32, 30, 28, 26, 24) if bullets else (44, 40, 36, 32, 28, 24)
    head_lines, head_font = _fit_wrapped(
        draw, headline, text_w, ENC_MAX_HEAD_LINES, head_sizes, bold=True
    )
    head_lh = int(head_font.size * 1.25) if hasattr(head_font, "size") else 40
    head_h = head_lh * len(head_lines)

    bullet_fits = [_fit_rich(draw, b, text_w - 28) for b in bullets]
    bullet_gap = 18
    bullet_hs = [int(sz * 1.4) * len(ln) for ln, sz in bullet_fits]
    bullets_h = sum(bullet_hs) + bullet_gap * max(0, len(bullets) - 1)
    head_to_bullets = 30 if bullets else 0

    divider_y = y1 - ENC_FOOTER_H
    body_top = pill_y + pill_h + 16
    body_h = divider_y - 16 - body_top
    content_h = head_h + head_to_bullets + bullets_h
    cur_y = body_top + max(0, (body_h - content_h) // 2)

    for line in head_lines:
        draw.text((cx0, cur_y), line, font=head_font, fill=(255, 255, 255))
        cur_y += head_lh
    cur_y += head_to_bullets

    for i, ((lines, size), block_h) in enumerate(
        zip(bullet_fits, bullet_hs, strict=True)
    ):
        lh = int(size * 1.4)
        color = bullet_colors[i % len(bullet_colors)]
        draw.ellipse(
            (cx0, cur_y + lh // 2 - 9, cx0 + 10, cur_y + lh // 2 + 1), fill=color
        )
        for ln in lines:
            px = cx0 + 28
            for j, (word, bold) in enumerate(ln):
                f = _get_font(size, bold=bold)
                if j:
                    px += max(3, size // 4)
                draw.text((px, cur_y), word, font=f, fill=(226, 232, 240))
                px += _text_width(draw, word, f)
            cur_y += lh
        cur_y += bullet_gap if block_h else 0

    # Divider + footer
    draw.line((cx0, divider_y, cx1, divider_y), fill=(78, 83, 93), width=1)
    cap_font = _get_font(13)
    cap_w = _text_width(draw, caption, cap_font)
    foot_cy = divider_y + (y1 - divider_y) // 2
    if signoff:
        sign_font = _get_font(16, bold=True)
        sign = _truncate_to_width(draw, signoff, sign_font, text_w - cap_w - 24)
        sb = draw.textbbox((0, 0), sign, font=sign_font)
        draw.text(
            (cx0, foot_cy - (sb[3] - sb[1]) // 2 - sb[1]),
            sign,
            font=sign_font,
            fill=signoff_color,
        )
    cb = draw.textbbox((0, 0), caption, font=cap_font)
    draw.text(
        (cx1 - cap_w, foot_cy - (cb[3] - cb[1]) // 2 - cb[1]),
        caption,
        font=cap_font,
        fill=(148, 163, 184),
    )


def _draw_daily_encouragement_slide(
    canvas: Image.Image,
    card: dict[str, Any],
    theme: SlideTheme | dict[str, Any] | None = None,
) -> None:
    """Render the daily encouragement slide over a local procedural dawn gradient."""
    _ = theme
    canvas.paste(_render_dawn_gradient(), (0, 0))
    raw = card.get("bullets")
    if not isinstance(raw, list) or not raw:
        raw = card.get("bullet_points")
    bullets = (
        [str(b).strip() for b in raw if str(b).strip()] if isinstance(raw, list) else []
    )
    _draw_frosted_takeaway_card(
        canvas,
        pill_label=ENC_PILL_LABEL,
        headline=str(card.get("title") or "").strip(),
        bullets=bullets,
        signoff=str(card.get("host_signoff") or "").strip(),
        caption=ENC_CAPTION,
        pill_fill=(245, 158, 11, 64),
        pill_border=(245, 158, 11),
        pill_text=(253, 230, 138),
        signoff_color=(253, 230, 138),
    )


def _draw_google_nest_weather_slide(  # noqa: C901, PLR0912, PLR0915
    canvas: Image.Image,
    card: dict[str, Any],
    theme: SlideTheme | dict[str, Any] | None = None,
) -> None:
    """Render Google Nest Hub atmospheric weather slide layout."""
    _ = theme
    w_data = card.get("weather_data") or {}
    condition = str(
        w_data.get("condition") or card.get("weather_condition") or "sunny"
    ).strip()

    # 1. Atmospheric 135-degree linear gradient canvas
    grad = _render_atmospheric_weather_gradient(condition)
    canvas.paste(grad, (0, 0))

    draw = ImageDraw.Draw(canvas)

    # 2. Location & condition header at Y: 35
    location = str(w_data.get("location") or card.get("location") or "").strip()
    if location:
        header_text = f"{location} • Today"
    else:
        header_text = str(card.get("title") or "Today's Weather • Today").strip()

    header_font = _get_font(20, bold=True)
    draw.text((45, 35), header_text, font=header_font, fill=(226, 232, 240))

    # 3. Hero row (Y: 70 to 260)
    raw_temp = w_data.get("current_temp")
    if raw_temp is None:
        raw_temp = card.get("current_temp")
    if raw_temp is not None:
        if isinstance(raw_temp, (int, float)):
            temp_text = f"{round(float(raw_temp))}°"
        else:
            temp_text = str(raw_temp).strip()
            if not temp_text.endswith(("°", "F", "C")):
                temp_text = f"{temp_text}°"
    else:
        temp_text = "--°"

    temp_font = _get_font(108, bold=True)
    draw.text((45, 70), temp_text, font=temp_font, fill=(255, 255, 255))
    temp_bbox = draw.textbbox((45, 70), temp_text, font=temp_font)
    temp_w = temp_bbox[2] - temp_bbox[0]

    info_x = 45 + temp_w + 24
    cond_text = str(
        w_data.get("condition_text")
        or card.get("condition_text")
        or condition.replace("_", " ").title()
    ).strip()

    cond_font = _get_font(32, bold=True)
    draw.text((info_x, 85), cond_text, font=cond_font, fill=(255, 255, 255))

    high = w_data.get("high")
    low = w_data.get("low")
    if high is not None and low is not None:
        h_str = f"{high}°" if not str(high).endswith("°") else str(high)
        l_str = f"{low}°" if not str(low).endswith("°") else str(low)
        hl_text = f"High {h_str} • Low {l_str}"
    elif high is not None:
        h_str = f"{high}°" if not str(high).endswith("°") else str(high)
        hl_text = f"High {h_str}"
    elif low is not None:
        l_str = f"{low}°" if not str(low).endswith("°") else str(low)
        hl_text = f"Low {l_str}"
    else:
        hl_text = ""

    if hl_text:
        hl_font = _get_font(20, bold=False)
        draw.text((info_x, 132), hl_text, font=hl_font, fill=(186, 230, 253))

    hero_icon = load_weather_icon(condition, size=(108, 108))
    if "A" in hero_icon.getbands():
        canvas.paste(hero_icon, (865, 75), mask=hero_icon.split()[3])
    else:
        canvas.paste(hero_icon, (865, 75))

    # 3b. Actionable preparation advisory chip (hero section, under High/Low)
    advisory = str(w_data.get("advisory") or "").strip()
    if advisory:
        _draw_advisory_chip(
            canvas,
            advisory,
            x=info_x,
            y=ADVISORY_CHIP_Y,
            max_w=ADVISORY_RIGHT_LIMIT - info_x,
        )
        draw = ImageDraw.Draw(canvas)

    # 4. Metrics chips (Y: 275 to 325)
    chips_data: list[tuple[str, str]] = []
    if (humidity := w_data.get("humidity")) is not None:
        h_val = f"{humidity}%" if not str(humidity).endswith("%") else str(humidity)
        chips_data.append(("humidity", f"Humidity: {h_val}"))

    if (wind_speed := w_data.get("wind_speed")) is not None:
        unit = w_data.get("wind_speed_unit") or "mph"
        chips_data.append(("wind", f"Wind: {wind_speed} {unit}"))

    precip = w_data.get("precipitation_probability")
    if precip is None:
        precip = w_data.get("precipitation")
    if precip is not None:
        p_val = f"{precip}%" if not str(precip).endswith("%") else str(precip)
        chips_data.append(("precip", f"Precip: {p_val}"))

    if chips_data:
        chip_x = 45
        chip_y = 278
        chip_h = 44
        chip_radius = 22
        chip_font = _get_font(16, bold=True)

        chip_overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        c_draw = ImageDraw.Draw(chip_overlay)

        for _, chip_text in chips_data:
            t_bbox = draw.textbbox((0, 0), chip_text, font=chip_font)
            text_w = t_bbox[2] - t_bbox[0]
            chip_w = max(110, text_w + 64)

            c_draw.rounded_rectangle(
                (chip_x, chip_y, chip_x + chip_w, chip_y + chip_h),
                radius=chip_radius,
                fill=(15, 23, 42, 130),
                outline=(255, 255, 255, 50),
                width=1,
            )
            chip_x += chip_w + 14

        comp = Image.alpha_composite(canvas.convert("RGBA"), chip_overlay).convert(
            "RGB"
        )
        canvas.paste(comp, (0, 0))
        draw = ImageDraw.Draw(canvas)

        chip_x = 45
        for icon_key, chip_text in chips_data:
            t_bbox = draw.textbbox((0, 0), chip_text, font=chip_font)
            text_w = t_bbox[2] - t_bbox[0]
            chip_w = max(110, text_w + 64)

            m_icon = load_weather_icon(icon_key, size=(24, 24))
            if "A" in m_icon.getbands():
                canvas.paste(m_icon, (chip_x + 14, chip_y + 10), mask=m_icon.split()[3])
            else:
                canvas.paste(m_icon, (chip_x + 14, chip_y + 10))

            draw.text(
                (chip_x + 46, chip_y + 13),
                chip_text,
                font=chip_font,
                fill=(241, 245, 249),
            )
            chip_x += chip_w + 14

    # 5. Google Nest Hourly Forecast Timeline (Y: 345 to 555)
    cont_x0, cont_y0 = 35, 345
    cont_x1, cont_y1 = 985, 555
    cont_w = cont_x1 - cont_x0

    cont_overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    o_draw = ImageDraw.Draw(cont_overlay)
    o_draw.rounded_rectangle(
        (cont_x0, cont_y0, cont_x1, cont_y1),
        radius=16,
        fill=(15, 23, 42, 128),
        outline=(255, 255, 255, 46),
        width=1,
    )
    comp = Image.alpha_composite(canvas.convert("RGBA"), cont_overlay).convert("RGB")
    canvas.paste(comp, (0, 0))
    draw = ImageDraw.Draw(canvas)

    forecast_points = w_data.get("forecast_points") or []
    if not forecast_points:
        empty_font = _get_font(18, bold=False)
        msg = "Hourly forecast unavailable"
        m_bbox = draw.textbbox((0, 0), msg, font=empty_font)
        m_w = m_bbox[2] - m_bbox[0]
        draw.text(
            (cont_x0 + (cont_w - m_w) // 2, cont_y0 + 95),
            msg,
            font=empty_font,
            fill=(148, 163, 184),
        )
    else:
        num_pts = min(len(forecast_points), 6)
        col_w = cont_w / num_pts
        time_font = _get_font(16, bold=True)
        temp_pt_font = _get_font(20, bold=True)

        for i in range(num_pts):
            pt = forecast_points[i]
            if not isinstance(pt, dict):
                continue
            col_center = cont_x0 + (i + 0.5) * col_w

            time_label = str(pt.get("time") or "").strip()
            if time_label:
                tb = draw.textbbox((0, 0), time_label, font=time_font)
                tw = tb[2] - tb[0]
                draw.text(
                    (int(col_center - tw / 2), cont_y0 + 22),
                    time_label,
                    font=time_font,
                    fill=(203, 213, 225),
                )

            pt_cond = str(pt.get("condition") or "sunny").strip()
            pt_icon = load_weather_icon(pt_cond, size=(32, 32))
            icon_x = int(col_center - 16)
            icon_y = cont_y0 + 64
            if "A" in pt_icon.getbands():
                canvas.paste(pt_icon, (icon_x, icon_y), mask=pt_icon.split()[3])
            else:
                canvas.paste(pt_icon, (icon_x, icon_y))

            pt_temp = pt.get("temperature")
            if pt_temp is not None:
                if isinstance(pt_temp, (int, float)):
                    p_temp_str = f"{round(float(pt_temp))}°"
                else:
                    p_temp_str = str(pt_temp).strip()
                    if not (
                        p_temp_str.endswith("°")
                        or "/" in p_temp_str
                        or " " in p_temp_str
                    ):
                        p_temp_str = f"{p_temp_str}°"
            else:
                p_temp_str = "--°"

            tpb = draw.textbbox((0, 0), p_temp_str, font=temp_pt_font)
            tpw = tpb[2] - tpb[0]
            draw.text(
                (int(col_center - tpw / 2), cont_y0 + 128),
                p_temp_str,
                font=temp_pt_font,
                fill=(255, 255, 255),
            )


def _commute_minutes(value: Any) -> str:
    """Format a commute duration as 'N' minutes text, or '--' when unknown."""
    if isinstance(value, bool) or value is None:
        return "--"
    try:
        return str(round(float(value)))
    except (TypeError, ValueError):
        return "--"


def _render_commute_map_background(map_path: Any) -> Image.Image:
    """Load the live route map cover-fitted to canvas, or draw a dark fallback map."""
    resolved = _resolve_image_path(map_path) if map_path else None
    if resolved is not None:
        try:
            with Image.open(resolved) as opened:
                rgb = opened.convert("RGB")
            scale = max(CANVAS_WIDTH / rgb.width, CANVAS_HEIGHT / rgb.height)
            new_size = (
                max(CANVAS_WIDTH, round(rgb.width * scale)),
                max(CANVAS_HEIGHT, round(rgb.height * scale)),
            )
            fitted = rgb.resize(new_size, Image.LANCZOS)
            left = (fitted.width - CANVAS_WIDTH) // 2
            top = (fitted.height - CANVAS_HEIGHT) // 2
            return fitted.crop((left, top, left + CANVAS_WIDTH, top + CANVAS_HEIGHT))
        except (OSError, ValueError):
            _LOGGER.debug("Commute map image unreadable: %s", resolved)

    bg = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), (22, 27, 34))
    d = ImageDraw.Draw(bg)
    for x in range(0, CANVAS_WIDTH, 80):
        d.line([(x, 0), (x, CANVAS_HEIGHT)], fill=(32, 38, 48), width=3)
    for y in range(0, CANVAS_HEIGHT, 80):
        d.line([(0, y), (CANVAS_WIDTH, y)], fill=(32, 38, 48), width=3)
    d.line([(0, 420), (CANVAS_WIDTH, 160)], fill=(48, 56, 70), width=18)
    return bg


COMMUTE_MARGIN = 30
COMMUTE_CHIP_GAP = 14
COMMUTE_CHIP_PADDING = 74
COMMUTE_CHIP_TEXT_INSET = 37
COMMUTE_HERO_X1 = 380
COMMUTE_HERO_TEXT_X = 52
CLOSING_SIDE_MARGIN = 60
CLOSING_TITLE_MAX_SIZE = 64
CLOSING_TITLE_MIN_SIZE = 36
MIN_COMMUTE_CHIPS = 2


def _layout_commute_chips(
    draw: ImageDraw.ImageDraw,
    chips: list[tuple[str, Any, str]],
    font: ImageFont.ImageFont,
) -> list[tuple[int, int, str, str]]:
    """
    Lay out mode comparison chips so every chip stays inside the canvas.

    Only modes with a known travel time are shown, and the row is omitted
    entirely unless at least two modes can be compared. Returns
    ``(x0, x1, mode, text)`` boxes with each label ellipsized to fit.
    """
    known = [c for c in chips if _commute_minutes(c[1]) != "--"]
    if len(known) < MIN_COMMUTE_CHIPS:
        return []
    avail = CANVAS_WIDTH - COMMUTE_MARGIN * 2 - COMMUTE_CHIP_GAP * (len(known) - 1)
    max_chip_w = avail // len(known)
    max_text_w = max_chip_w - COMMUTE_CHIP_PADDING
    boxes: list[tuple[int, int, str, str]] = []
    chip_x = COMMUTE_MARGIN
    for mode, minutes, label in known:
        text = _truncate_to_width(
            draw, f"{_commute_minutes(minutes)} min • {label}", font, max_text_w
        )
        width = min(_text_width(draw, text, font) + COMMUTE_CHIP_PADDING, max_chip_w)
        boxes.append((chip_x, chip_x + width, mode, text))
        chip_x += width + COMMUTE_CHIP_GAP
    return boxes


def _commute_eta_text(c_data: dict[str, Any]) -> str:
    """Build the hero card arrival line (clock ETA or relative trip length)."""
    eta = str(c_data.get("eta") or "").strip()
    distance = str(c_data.get("distance") or "").strip()
    parts = []
    if eta:
        parts.append(eta if c_data.get("eta_is_relative") else f"Arrive around {eta}")
    if distance:
        parts.append(distance)
    return " • ".join(parts)


def _commute_round_trip_text(c_data: dict[str, Any]) -> str:
    """Build the hero card's trip-home line, or '' without an evening estimate."""
    home = c_data.get("return_time")
    total = c_data.get("round_trip_time")
    if home is None or total is None:
        return ""
    hours, mins = divmod(int(total), 60)
    total_text = f"{hours}h {mins:02d}m" if hours else f"{mins} min"
    return f"Home {_commute_minutes(home)} min • Round trip {total_text}"


def _draw_closing_slide(canvas: Image.Image, card: dict[str, Any]) -> None:
    """Render the sign-off as a full-bleed photo with a bold, centered send-off."""
    img, _ = _load_image(card)
    if img is None:
        img, _ = _load_image({"image_path": "closing_good_day.jpg"})
    base = _render_photo_background(img) if img is not None else _render_dawn_gradient()
    canvas.paste(base.convert("RGB"), (0, 0))

    # Fade the lower half to deep navy so the sign-off reads on any photo.
    fade = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(fade)
    start = CANVAS_HEIGHT * 0.42
    for y in range(int(start), CANVAS_HEIGHT):
        t = (y - start) / (CANVAS_HEIGHT - start)
        fd.line([(0, y), (CANVAS_WIDTH, y)], fill=(8, 15, 35, int(225 * t**1.3)))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), fade).convert("RGB"))
    draw = ImageDraw.Draw(canvas)

    max_w = CANVAS_WIDTH - 2 * CLOSING_SIDE_MARGIN
    title = str(card.get("title") or "").strip()
    size = CLOSING_TITLE_MAX_SIZE
    font = _get_font(size, bold=True)
    while size > CLOSING_TITLE_MIN_SIZE and _text_width(draw, title, font) > max_w:
        size -= 2
        font = _get_font(size, bold=True)
    title = _truncate_to_width(draw, title, font, max_w)
    title_w = _text_width(draw, title, font)
    title_y = CANVAS_HEIGHT - 150
    draw.text(
        ((CANVAS_WIDTH - title_w) // 2 + 2, title_y + 3),
        title,
        font=font,
        fill=(0, 0, 0),
    )
    draw.text(
        ((CANVAS_WIDTH - title_w) // 2, title_y), title, font=font, fill=(255, 255, 255)
    )

    if subtitle := str(card.get("subtitle") or "").strip():
        pill_font = _get_font(22, bold=True)
        subtitle = _truncate_to_width(draw, subtitle.upper(), pill_font, max_w - 48)
        pill_w = _text_width(draw, subtitle, pill_font) + 48
        x0 = (CANVAS_WIDTH - pill_w) // 2
        y0 = title_y - 62
        draw.rounded_rectangle(
            (x0, y0, x0 + pill_w, y0 + 40), radius=20, fill=(251, 191, 36)
        )
        draw.text((x0 + 24, y0 + 8), subtitle, font=pill_font, fill=(30, 27, 75))


def _draw_google_nest_commute_slide(  # noqa: PLR0915
    canvas: Image.Image,
    card: dict[str, Any],
    theme: SlideTheme | dict[str, Any] | None = None,
) -> None:
    """Render the Google Nest Hub commute HUD over the live route map."""
    _ = theme
    c_data = card.get("commute_data") or {}
    selected = str(c_data.get("selected_mode") or "driving").lower()
    faster = str(c_data.get("faster_mode") or selected).lower()
    drive_time = c_data.get("drive_time")
    transit_time = c_data.get("transit_time")
    diff = c_data.get("diff_minutes")
    drive_label = str(c_data.get("drive_summary") or "Driving")
    transit_label = str(c_data.get("transit_summary") or "Transit")
    is_faster = (
        isinstance(diff, (int, float))
        and not isinstance(diff, bool)
        and diff > 0
        and selected == faster
    )

    canvas.paste(_render_commute_map_background(c_data.get("map_image_path")), (0, 0))

    # Readability vignette over the map.
    vignette = Image.new("RGBA", canvas.size, (15, 23, 42, 115))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), vignette).convert("RGB"))

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    # Hero ETA card and mode chips (frosted slate).
    round_trip_text = _commute_round_trip_text(c_data)
    hero_y1 = 235 if round_trip_text else 205
    od.rounded_rectangle(
        (COMMUTE_MARGIN, 68, COMMUTE_HERO_X1, hero_y1),
        radius=16,
        fill=(15, 23, 42, 224),
        outline=(255, 255, 255, 36),
        width=1,
    )
    chip_y0, chip_y1 = 515, 581
    chip_font = _get_font(20, bold=True)
    chip_boxes = _layout_commute_chips(
        od,
        [
            ("driving", drive_time, drive_label),
            ("transit", transit_time, transit_label),
        ],
        chip_font,
    )
    for x0, x1, mode, _text in chip_boxes:
        is_sel = mode == selected
        od.rounded_rectangle(
            (x0, chip_y0, x1, chip_y1),
            radius=14,
            fill=(2, 132, 199, 90) if is_sel else (15, 23, 42, 199),
            outline=(56, 189, 248, 255) if is_sel else (255, 255, 255, 31),
            width=2 if is_sel else 1,
        )

    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB"))
    draw = ImageDraw.Draw(canvas)

    # Header pill + subtitle.
    pill_font = _get_font(16, bold=True)
    pill_text = "COMMUTE TO WORK"
    pw = draw.textbbox((0, 0), pill_text, font=pill_font)[2]
    draw.rounded_rectangle((30, 22, 30 + pw + 32, 52), radius=15, fill=(2, 132, 199))
    draw.text((46, 28), pill_text, font=pill_font, fill=(255, 255, 255))
    route_word = "Fastest route" if is_faster else "Route"
    summary = str(c_data.get("summary") or "").strip()
    traffic = str(c_data.get("traffic_condition") or "").strip()
    sub_parts = [f"{route_word} {summary}".strip()]
    if traffic:
        sub_parts.append(f"{traffic.capitalize()} traffic")
    sub_x = 30 + pw + 48
    sub_font = _get_font(16)
    draw.text(
        (sub_x, 28),
        _truncate_to_width(
            draw, " • ".join(sub_parts), sub_font, CANVAS_WIDTH - COMMUTE_MARGIN - sub_x
        ),
        font=sub_font,
        fill=(203, 213, 225),
    )

    # Hero card content.
    sel_minutes = drive_time if selected == "driving" else transit_time
    dur_font = _get_font(56, bold=True)
    dur_text = _commute_minutes(sel_minutes)
    draw.text((52, 80), dur_text, font=dur_font, fill=(16, 185, 129))
    dw = draw.textbbox((52, 80), dur_text, font=dur_font)[2]
    draw.text((dw + 8, 108), "min", font=_get_font(26, bold=True), fill=(16, 185, 129))
    if is_faster:
        badge = f"FASTER BY {_commute_minutes(diff)} MIN"
        bfont = _get_font(14, bold=True)
        bw = draw.textbbox((0, 0), badge, font=bfont)[2]
        bx1 = 366
        draw.rounded_rectangle(
            (bx1 - bw - 20, 92, bx1, 120), radius=14, fill=(6, 95, 70)
        )
        draw.text((bx1 - bw - 10, 100), badge, font=bfont, fill=(52, 211, 153))

    if eta_text := _commute_eta_text(c_data):
        eta_font = _get_font(18)
        draw.text(
            (COMMUTE_HERO_TEXT_X, 160),
            _truncate_to_width(
                draw,
                eta_text,
                eta_font,
                COMMUTE_HERO_X1 - COMMUTE_HERO_TEXT_X - COMMUTE_CHIP_GAP,
            ),
            font=eta_font,
            fill=(148, 163, 184),
        )
    if round_trip_text:
        trip_font = _get_font(18, bold=True)
        draw.text(
            (COMMUTE_HERO_TEXT_X, 194),
            _truncate_to_width(
                draw,
                round_trip_text,
                trip_font,
                COMMUTE_HERO_X1 - COMMUTE_HERO_TEXT_X - COMMUTE_CHIP_GAP,
            ),
            font=trip_font,
            fill=(125, 211, 252),
        )

    # Mode chip text.
    for x0, _x1, mode, text in chip_boxes:
        draw.text(
            (x0 + COMMUTE_CHIP_TEXT_INSET, chip_y0 + 21),
            text,
            font=chip_font,
            fill=(255, 255, 255) if mode == selected else (203, 213, 225),
        )


def _resolve_image_path(raw_path: Any) -> Path | None:  # noqa: PLR0911
    """Resolve an image path from filesystem, bundled assets, or asset name."""
    if not raw_path or not isinstance(raw_path, (str, Path)):
        return None

    path_obj = Path(raw_path).expanduser()
    if path_obj.is_file():
        return path_obj

    p_str = str(raw_path).strip()
    if not p_str:
        return None

    # Handle Home Assistant /local/ path mapped to /config/www or ./www
    if p_str.startswith("/local/"):
        rel = p_str.removeprefix("/local/")
        for base_dir in (Path("/config/www"), Path("www"), Path("./www")):
            candidate = base_dir / rel
            if candidate.is_file():
                return candidate

    # Handle Home Assistant /api/image/serve/<image_id>/...
    if "/api/image/serve/" in p_str:
        image_id = p_str.split("/api/image/serve/")[1].split("/")[0]
        for base_dir in (
            Path("/config/image"),
            Path("/config/.storage/image_upload"),
            Path(".storage/image_upload"),
            Path("image"),
        ):
            candidate = base_dir / image_id
            if candidate.is_file():
                return candidate

    # Check direct name, filename, and .png/.jpg/.jpeg stems inside bundled assets
    candidates = (
        BUNDLED_ASSETS_DIR / p_str,
        BUNDLED_ASSETS_DIR / path_obj.name,
        BUNDLED_ASSETS_DIR / f"{path_obj.stem}.png",
        BUNDLED_ASSETS_DIR / f"{path_obj.stem}.jpg",
        BUNDLED_ASSETS_DIR / f"{path_obj.stem}.jpeg",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    return None


def _load_image(card_data: dict[str, Any]) -> tuple[Image.Image | None, bool]:
    """Inspect card_data for image/icon references, resolve and load into memory."""
    sources_to_try = [
        card_data.get("image_path"),
        card_data.get("icon_path"),
        card_data.get("icon"),
        card_data.get("image"),
        card_data.get("entity_picture"),
        card_data.get("avatar_image"),
        card_data.get("avatar_fallback"),
        card_data.get("fallback_image"),
    ]
    candidate_sources = [s for s in sources_to_try if s and isinstance(s, (str, Path))]
    if not candidate_sources:
        return None, False

    for raw_source in candidate_sources:
        resolved_path = _resolve_image_path(raw_source)
        if resolved_path is not None and resolved_path.is_file():
            try:
                with Image.open(resolved_path) as raw_img:
                    if raw_img.width <= 0 or raw_img.height <= 0:
                        _LOGGER.warning("Image '%s' has zero dimensions", resolved_path)
                        continue

                    has_alpha = False
                    if "A" in raw_img.getbands():
                        alpha_extrema = raw_img.getextrema()[-1]
                        if (
                            isinstance(alpha_extrema, tuple)
                            and alpha_extrema[0] < OPAQUE_ALPHA
                        ):
                            has_alpha = True
                    elif raw_img.info.get("transparency") is not None:
                        has_alpha = True

                    img = raw_img.convert("RGBA")
                    return img, has_alpha
            except (OSError, ValueError) as err:
                _LOGGER.warning(
                    "Failed to open image file '%s': %s; trying next candidate",
                    resolved_path,
                    err,
                )

    _LOGGER.warning(
        "Image file '%s' not found; falling back to typography slide",
        candidate_sources[0],
    )
    return None, False


def _determine_layout_mode(
    card_data: dict[str, Any],
    img: Image.Image | None,
    *,
    has_alpha: bool,
) -> str:
    """Determine visual composition layout: 'card' (default), 'icon', or 'photo'."""
    if img is None:
        return "card"

    explicit_layout = (
        str(
            card_data.get("layout")
            or card_data.get("image_style")
            or card_data.get("image_mode")
            or ""
        )
        .strip()
        .lower()
    )

    if explicit_layout in ("photo", "background", "full", "full_bleed", "fullscreen"):
        return "photo"
    if explicit_layout in ("icon", "side_by_side", "avatar", "badge", "card"):
        return "icon"

    is_icon_key = (
        "icon" in card_data or "icon_path" in card_data
    ) and "image_path" not in card_data
    aspect = (img.width / img.height) if img.height > 0 else 1.0
    if is_icon_key or has_alpha or aspect < ASPECT_RATIO_WIDESCREEN_THRESHOLD:
        return "icon"

    return "photo"


def _render_photo_background(img: Image.Image) -> Image.Image:
    """Render 1024x600 photo background with aspect-fill crop and dark vignette."""
    orig_w, orig_h = img.size
    if orig_w <= 0 or orig_h <= 0:
        return _render_gradient_background()

    scale = max(CANVAS_WIDTH / orig_w, CANVAS_HEIGHT / orig_h)
    new_w = max(CANVAS_WIDTH, round(orig_w * scale))
    new_h = max(CANVAS_HEIGHT, round(orig_h * scale))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    crop_left = max(0, (new_w - CANVAS_WIDTH) // 2)
    crop_top = max(0, (new_h - CANVAS_HEIGHT) // 2)
    base = resized.crop(
        (crop_left, crop_top, crop_left + CANVAS_WIDTH, crop_top + CANVAS_HEIGHT)
    ).convert("RGBA")

    # Dark vignette overlay to guarantee contrast across the entire frame
    vignette = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    v_draw = ImageDraw.Draw(vignette)

    center_y = CANVAS_HEIGHT / 2
    for y in range(CANVAS_HEIGHT):
        dist = abs(y - center_y) / center_y
        # Soft ambient vignette: edge darkening only (alpha 15 to 60)
        line_alpha = min(255, int(15 + 45 * (dist**2)))
        v_draw.line(
            [(0, y), (CANVAS_WIDTH, y)],
            fill=(2, 6, 23, line_alpha),
        )

    return Image.alpha_composite(base, vignette).convert("RGB")


def _composite_icon(
    canvas: Image.Image,
    img: Image.Image,
    *,
    has_alpha: bool,
) -> tuple[int, int]:
    """
    Composite avatar or icon on the left side of the card.

    Returns updated text content boundaries (content_left, content_right).
    """
    img_w, img_h = img.size
    if img_w <= 0 or img_h <= 0:
        return CONTENT_LEFT, CONTENT_RIGHT

    scale = min(ICON_BOX_MAX_W / img_w, ICON_BOX_MAX_H / img_h)
    fit_w = max(1, round(img_w * scale))
    fit_h = max(1, round(img_h * scale))
    fit_img = img.resize((fit_w, fit_h), Image.Resampling.LANCZOS)

    pos_x = ICON_BOX_X + (ICON_BOX_MAX_W - fit_w) // 2
    pos_y = ICON_CENTER_Y - (fit_h // 2)

    if has_alpha:
        canvas.paste(fit_img, (pos_x, pos_y), mask=fit_img.split()[3])
    else:
        mask = Image.new("L", (fit_w, fit_h), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle((0, 0, fit_w, fit_h), radius=20, fill=255)
        canvas.paste(fit_img, (pos_x, pos_y), mask=mask)

    content_left = ICON_BOX_X + ICON_BOX_MAX_W + 36
    content_right = CONTENT_RIGHT
    return content_left, content_right


def _draw_header(  # noqa: PLR0913
    draw: ImageDraw.ImageDraw,
    card_data: dict[str, Any],
    start_y: int,
    content_left: int = CONTENT_LEFT,
    content_right: int = CONTENT_RIGHT,
    theme: dict[str, Any] | None = None,
) -> int:
    """Draw optional category badge, title, subtitle, and divider line."""
    max_text_width = content_right - content_left
    accent_color = (theme or {}).get("accent_color", SUBTITLE_COLOR)
    title = str(card_data.get("title", "")).strip()

    raw_badge = str(card_data.get("badge") or (theme or {}).get("badge") or "").strip()
    is_alert = (
        bool(card_data.get("is_alert"))
        or str(card_data.get("badge_type", "")).lower() == "alert"
        or str(card_data.get("step_name", "")).lower() in ("alert", "warning")
    )
    show_badge = bool(card_data.get("show_badge", False))

    badge_text = ""
    if raw_badge:
        lower_badge = raw_badge.lower()
        lower_title = title.lower()
        duplicates_title = any(
            w in lower_title for w in lower_badge.split() if len(w) > MIN_BADGE_WORD_LEN
        ) or any(
            w in lower_badge for w in lower_title.split() if len(w) > MIN_BADGE_WORD_LEN
        )
        if is_alert or show_badge or (not duplicates_title and "badge" in card_data):
            badge_text = raw_badge

    title_font = _get_font(34, bold=True)
    subtitle_font = _get_font(19, bold=False)
    curr_y = start_y

    if badge_text:
        badge_font = _get_font(14, bold=True)
        b_bbox = draw.textbbox((0, 0), badge_text.upper(), font=badge_font)
        b_w = (b_bbox[2] - b_bbox[0]) + 20
        b_h = (b_bbox[3] - b_bbox[1]) + 8
        draw.rounded_rectangle(
            (content_left, curr_y, content_left + b_w, curr_y + b_h),
            radius=b_h // 2,
            outline=accent_color,
            width=1,
        )
        draw.text(
            (content_left + 10, curr_y + 3),
            badge_text.upper(),
            font=badge_font,
            fill=accent_color,
        )
        curr_y += b_h + 10

    if title:
        title_lines = _wrap_text(title, title_font, max_text_width, draw)
        for line in title_lines:
            bbox = draw.textbbox((content_left, curr_y), line, font=title_font)
            line_height = bbox[3] - bbox[1]
            draw.text((content_left, curr_y), line, font=title_font, fill=TITLE_COLOR)
            curr_y += line_height + 6

    subtitle = str(card_data.get("subtitle", "")).strip()
    if subtitle:
        curr_y += 2
        sub_lines = _wrap_text(subtitle, subtitle_font, max_text_width, draw)
        for line in sub_lines:
            bbox = draw.textbbox((content_left, curr_y), line, font=subtitle_font)
            line_height = bbox[3] - bbox[1]
            draw.text(
                (content_left, curr_y), line, font=subtitle_font, fill=accent_color
            )
            curr_y += line_height + 4

    if title or subtitle:
        curr_y += 10
        draw.line(
            [(content_left, curr_y), (content_right, curr_y)],
            fill=DIVIDER_COLOR,
            width=1,
        )
        curr_y += 16

    return curr_y


def _draw_two_column_agenda(  # noqa: C901, PLR0912, PLR0915
    canvas_or_draw: Image.Image | ImageDraw.ImageDraw,
    card_or_start_y: dict[str, Any] | int = 0,
    theme_or_card: SlideTheme | dict[str, Any] | int | None = None,
    theme: SlideTheme | dict[str, Any] | None = None,
    canvas: Image.Image | None = None,
) -> int:
    """Render Option B Dual-Focused Agenda: meetings left, to-dos right."""
    if isinstance(canvas_or_draw, Image.Image):
        actual_canvas = canvas_or_draw
        card = card_or_start_y if isinstance(card_or_start_y, dict) else {}
        actual_theme = (
            theme
            if isinstance(theme, dict)
            else (
                theme_or_card
                if isinstance(theme_or_card, dict)
                else resolve_slide_theme(card)
            )
        )
    else:
        # Legacy caller passed (draw, card_data, start_y, theme=theme, canvas=canvas)
        actual_canvas = (
            canvas
            if canvas is not None
            else Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT))
        )
        card = (
            card_or_start_y
            if isinstance(card_or_start_y, dict)
            else (theme_or_card if isinstance(theme_or_card, dict) else {})
        )
        actual_theme = theme if isinstance(theme, dict) else resolve_slide_theme(card)

    # 1. Background
    img, _ = _load_image(card)
    if img is not None:
        bg = _render_photo_background(img)
        actual_canvas.paste(bg, (0, 0))
    else:
        bg = _render_gradient_background(theme=actual_theme)
        actual_canvas.paste(bg, (0, 0))

    draw = ImageDraw.Draw(actual_canvas)

    # 2. Extract structured agenda data (with fallback to legacy bullets/items)
    raw_agenda = card.get("agenda_data")
    agenda_data: dict[str, Any] = raw_agenda if isinstance(raw_agenda, dict) else {}

    # Resolve date string
    date_str = str(agenda_data.get("date_str") or "").strip()
    if not date_str:
        if card.get("date_str"):
            date_str = str(card["date_str"]).strip()
        elif card.get("subtitle"):
            sub = str(card["subtitle"]).strip()
            date_str = sub.split(" • ")[0].strip() if " • " in sub else sub
        else:
            now_dt = dt_util.now()
            try:
                date_str = now_dt.strftime("%A, %b %-d")
            except (ValueError, TypeError):
                date_str = f"{now_dt.strftime('%A, %b')} {now_dt.day}"

    # Resolve calendar events
    events: list[dict[str, str]] = []
    if "calendar_events" in agenda_data and isinstance(
        agenda_data["calendar_events"], list
    ):
        events.extend(
            {
                "time": str(ev.get("time") or "").strip(),
                "title": str(
                    ev.get("title")
                    or ev.get("summary")
                    or ev.get("message")
                    or "Scheduled Event"
                ).strip(),
                "location": str(ev.get("location") or "").strip(),
            }
            for ev in agenda_data["calendar_events"]
            if isinstance(ev, dict)
        )
    elif "calendar_items" in card and isinstance(card["calendar_items"], list):
        for item in card["calendar_items"]:
            if isinstance(item, dict):
                events.append(
                    {
                        "time": str(item.get("time") or "").strip(),
                        "title": str(
                            item.get("title")
                            or item.get("summary")
                            or "Scheduled Event"
                        ).strip(),
                        "location": str(item.get("location") or "").strip(),
                    }
                )
            elif isinstance(item, str) and item.strip():
                raw = item.strip()
                t_part = ""
                d_part = raw
                for sep in (" — ", " - ", " : "):
                    if sep in raw:
                        parts = raw.split(sep, 1)
                        t_part = parts[0].strip()
                        d_part = parts[1].strip()
                        break
                events.append({"time": t_part, "title": d_part, "location": ""})

    # Resolve todo items
    tasks: list[str] = []
    if "todo_items" in agenda_data and isinstance(agenda_data["todo_items"], list):
        for t in agenda_data["todo_items"]:
            if isinstance(t, dict):
                s = t.get("summary") or t.get("description") or t.get("title")
                if s and str(s).strip():
                    tasks.append(str(s).strip())
            elif isinstance(t, str) and t.strip():
                tasks.append(t.strip())
    elif "todo_items" in card and isinstance(card["todo_items"], list):
        for t in card["todo_items"]:
            if isinstance(t, dict):
                s = t.get("summary") or t.get("description") or t.get("title")
                if s and str(s).strip():
                    tasks.append(str(s).strip())
            elif isinstance(t, str) and t.strip():
                tasks.append(t.strip())
    elif (
        "bullet_points" in card
        and isinstance(card["bullet_points"], list)
        and not events
    ):
        tasks.extend(
            str(bp).strip() for bp in card["bullet_points"] if bp and str(bp).strip()
        )

    # 3. Header (Y: 30 to 75)
    # Title on left: TODAY'S SCHEDULE & TASKS (#f8fafc, size 26)
    title_text = str(card.get("title") or "TODAY'S SCHEDULE & TASKS").upper()
    title_font = _get_font(26, bold=True)
    draw.text((35, 36), title_text, font=title_font, fill=(248, 250, 252))

    # Date Pill on right: background #0284c7, text white, font size 18
    date_font = _get_font(18, bold=True)
    tb = draw.textbbox((0, 0), date_str, font=date_font)
    tw = tb[2] - tb[0]
    th = tb[3] - tb[1]
    pill_pad_x = 18
    pill_pad_y = 8
    pill_w = tw + (pill_pad_x * 2)
    pill_h = th + (pill_pad_y * 2)
    pill_x1 = 985
    pill_x0 = pill_x1 - pill_w
    pill_y0 = 33
    pill_y1 = pill_y0 + pill_h
    draw.rounded_rectangle(
        [(pill_x0, pill_y0), (pill_x1, pill_y1)],
        radius=pill_h // 2,
        fill=(2, 132, 199),
    )
    draw.text(
        (pill_x0 + pill_pad_x, pill_y0 + pill_pad_y - 1),
        date_str,
        font=date_font,
        fill=(255, 255, 255),
    )

    # 4. Frosted Slate Panels (#1e293b with accent borders)
    # Left: X: 35 to 495, width 460, fill #1e293b, border #38bdf8
    # Right: X: 525 to 985, width 460, fill #1e293b, border #10b981
    panel_overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    d_panel = ImageDraw.Draw(panel_overlay)
    col_y0 = 88
    col_y1 = 565

    d_panel.rounded_rectangle(
        [(35, col_y0), (495, col_y1)],
        radius=16,
        fill=(30, 41, 59, 230),
        outline=(56, 189, 248, 220),
        width=2,
    )
    d_panel.rounded_rectangle(
        [(525, col_y0), (985, col_y1)],
        radius=16,
        fill=(30, 41, 59, 230),
        outline=(16, 185, 129, 220),
        width=2,
    )
    comp = Image.alpha_composite(actual_canvas.convert("RGBA"), panel_overlay).convert(
        "RGB"
    )
    actual_canvas.paste(comp, (0, 0))
    draw = ImageDraw.Draw(actual_canvas)

    # 5. Left Column Content: Meetings & Events
    header_font = _get_font(18, bold=True)
    draw.text((55, 108), "📅 MEETINGS & EVENTS", font=header_font, fill=(56, 189, 248))
    draw.line([(55, 138), (475, 138)], fill=(51, 65, 85, 160), width=1)

    ev_y = 152
    if not events:
        empty_font = _get_font(16, bold=False)
        draw.text(
            (55, ev_y),
            "Schedule is wide open today.\nNo meetings scheduled.",
            font=empty_font,
            fill=(148, 163, 184),
            spacing=6,
        )
        ev_y += 60
    else:
        time_chip_font = _get_font(15, bold=True)
        ev_title_font = _get_font(18, bold=True)
        loc_font = _get_font(14, bold=False)

        for ev in events[:4]:
            if ev_y + 40 > col_y1 - 10:
                break
            time_str = ev.get("time", "")
            title_str = ev.get("title", "Scheduled Event")
            location_str = ev.get("location", "")

            # Time chip (Sky-400 #38bdf8, font 15, bold)
            if time_str:
                chip_tb = draw.textbbox((0, 0), time_str, font=time_chip_font)
                chip_w = chip_tb[2] - chip_tb[0] + 16
                chip_h = chip_tb[3] - chip_tb[1] + 8
                draw.rounded_rectangle(
                    [(55, ev_y), (55 + chip_w, ev_y + chip_h)],
                    radius=6,
                    fill=(15, 23, 42),
                    outline=(56, 189, 248, 180),
                    width=1,
                )
                draw.text(
                    (63, ev_y + 3),
                    time_str,
                    font=time_chip_font,
                    fill=(56, 189, 248),
                )
                ev_y += chip_h + 6

            # Bold title (#ffffff, font 18)
            wrapped_title = _wrap_text(
                title_str, ev_title_font, max_width=410, draw=draw
            )
            for t_line in wrapped_title[:2]:
                draw.text((55, ev_y), t_line, font=ev_title_font, fill=(255, 255, 255))
                ev_y += 23

            # Location subtext (#94a3b8, font 14)
            if location_str:
                loc_text = (
                    location_str
                    if location_str.startswith("📍")
                    else f"📍 {location_str}"
                )
                draw.text((55, ev_y), loc_text, font=loc_font, fill=(148, 163, 184))
                ev_y += 18

            ev_y += 12

    # 6. Right Column Content: Pending To-Dos
    todo_count = len(tasks)
    draw.text(
        (545, 108),
        f"✓ PENDING TO-DOS ({todo_count})",
        font=header_font,
        fill=(52, 211, 153),
    )
    draw.line([(545, 138), (965, 138)], fill=(51, 65, 85, 160), width=1)

    task_y = 152
    if not tasks:
        empty_font = _get_font(16, bold=False)
        draw.text(
            (545, task_y),
            "All caught up!\nNo pending to-do tasks.",
            font=empty_font,
            fill=(148, 163, 184),
            spacing=6,
        )
        task_y += 60
    else:
        task_font = _get_font(17, bold=False)
        cb_size = 18

        for task in tasks[:5]:
            if task_y + 30 > col_y1 - 10:
                break
            # Checkbox [ ] (Emerald-400, font 18)
            draw.rounded_rectangle(
                [(545, task_y + 2), (545 + cb_size, task_y + 2 + cb_size)],
                radius=4,
                outline=(52, 211, 153),
                width=2,
            )
            text_x = 545 + cb_size + 14
            max_w = 965 - text_x
            wrapped_task = _wrap_text(task, task_font, max_width=max_w, draw=draw)
            for line in wrapped_task[:2]:
                draw.text((text_x, task_y), line, font=task_font, fill=(241, 245, 249))
                task_y += 22
            task_y += 14

    return max(ev_y, task_y)


def _draw_bullets(  # noqa: PLR0913
    draw: ImageDraw.ImageDraw,
    raw_bullets: list[Any],
    start_y: int,
    content_left: int = CONTENT_LEFT,
    content_right: int = CONTENT_RIGHT,
    theme: dict[str, Any] | None = None,
) -> int:
    """Draw bullet points with dot indicators and text wrapping."""
    dot_color = (theme or {}).get("accent_color", BULLET_DOT_COLOR)
    bullet_font = _get_font(24, bold=False)
    bullet_indent = content_left + 28
    bullet_max_width = content_right - bullet_indent
    dot_radius = 4
    curr_y = start_y

    for bp in raw_bullets:
        bullet_text = str(bp).strip()
        if not bullet_text:
            continue

        if curr_y + 30 > CARD_Y1 - 20:
            break

        wrapped_lines = _wrap_text(bullet_text, bullet_font, bullet_max_width, draw)
        first_bbox = draw.textbbox(
            (bullet_indent, curr_y), wrapped_lines[0], font=bullet_font
        )
        line_height = first_bbox[3] - first_bbox[1]
        dot_center_y = curr_y + (line_height // 2)
        dot_x = content_left + 8
        draw.ellipse(
            [
                (dot_x - dot_radius, dot_center_y - dot_radius),
                (dot_x + dot_radius, dot_center_y + dot_radius),
            ],
            fill=dot_color,
        )

        for line in wrapped_lines:
            bbox = draw.textbbox((bullet_indent, curr_y), line, font=bullet_font)
            lh = bbox[3] - bbox[1]
            draw.text(
                (bullet_indent, curr_y),
                line,
                font=bullet_font,
                fill=BULLET_TEXT_COLOR,
            )
            curr_y += lh + 6

        curr_y += 10

    return curr_y


def render_slide(  # noqa: PLR0912, PLR0915
    card_data: dict[str, Any],
    output_jpeg_path: str,
    phonetic_name: str | None = None,
) -> str:
    """Render a structured visual card to a 1024x600 high-contrast RGB JPEG or PNG."""
    if not isinstance(card_data, dict):
        msg = "card_data must be a dict"
        raise InvalidCardDataError(msg)

    # 1. Resolve slide envelope if passed {step_name: ..., card: {...}}
    if "card" in card_data and isinstance(card_data["card"], dict):
        effective_card = {k: v for k, v in card_data.items() if k != "card"}
        effective_card.update(card_data["card"])
        step_name = (
            str(
                effective_card.get("step_name")
                or card_data.get("step_name")
                or effective_card.get("type")
                or ""
            )
            .strip()
            .lower()
        )
    else:
        effective_card = dict(card_data)
        step_name = (
            str(effective_card.get("step_name") or effective_card.get("type") or "")
            .strip()
            .lower()
        )

    # 2. Enforce identity contract before generating visual pixels
    _assert_identity_contract(effective_card, phonetic_name=phonetic_name)

    # 3. Google Nest Hub Weather slide layout
    if step_name == "weather" and effective_card.get("weather_data"):
        theme = resolve_slide_theme(effective_card)
        canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT))
        _draw_google_nest_weather_slide(canvas, effective_card, theme=theme)
        output_path = Path(output_jpeg_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() == ".png":
            canvas.save(str(output_path), "PNG")
        else:
            canvas.save(str(output_path), "JPEG", quality=95)
        return str(output_path)

    # 3a. Google Nest Hub Commute slide layout
    if step_name == "commute" and effective_card.get("commute_data"):
        theme = resolve_slide_theme(effective_card)
        canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT))
        _draw_google_nest_commute_slide(canvas, effective_card, theme=theme)
        output_path = Path(output_jpeg_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() == ".png":
            canvas.save(str(output_path), "PNG")
        else:
            canvas.save(str(output_path), "JPEG", quality=95)
        return str(output_path)

    # 3d. Closing sign-off (and the all-heard line) layout
    if step_name in ("closing", "all_heard"):
        canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT))
        _draw_closing_slide(canvas, effective_card)
        output_path = Path(output_jpeg_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() == ".png":
            canvas.save(str(output_path), "PNG")
        else:
            canvas.save(str(output_path), "JPEG", quality=95)
        return str(output_path)

    # 3c. Daily encouragement slide layout
    if step_name in ("daily_encouragement", "encouragement"):
        theme = resolve_slide_theme(effective_card)
        canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT))
        _draw_daily_encouragement_slide(canvas, effective_card, theme=theme)
        output_path = Path(output_jpeg_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() == ".png":
            canvas.save(str(output_path), "PNG")
        else:
            canvas.save(str(output_path), "JPEG", quality=95)
        return str(output_path)

    # 3b. Dual-Focused Agenda slide layout
    if (
        step_name in ("agenda", "calendar")
        or "agenda_data" in effective_card
        or effective_card.get("layout") == "agenda_split"
        or ("calendar_items" in effective_card and "todo_items" in effective_card)
    ):
        theme = resolve_slide_theme(effective_card)
        canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT))
        _draw_two_column_agenda(canvas, effective_card, theme=theme)
        output_path = Path(output_jpeg_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() == ".png":
            canvas.save(str(output_path), "PNG")
        else:
            canvas.save(str(output_path), "JPEG", quality=95)
        return str(output_path)

    card_data = effective_card

    # 4. Resolve visual theme and inspect/load image asset if present
    theme = resolve_slide_theme(card_data)
    img, has_alpha = _load_image(card_data)
    layout_mode = _determine_layout_mode(card_data, img, has_alpha=has_alpha)

    # 5. Render base background and card overlay
    if layout_mode == "photo" and img is not None:
        canvas = _render_photo_background(img)
        canvas = _render_card_overlay(canvas, theme=theme, is_photo=True)
        content_left = CONTENT_LEFT
        content_right = CONTENT_RIGHT
    else:
        canvas = _render_gradient_background(theme=theme)
        canvas = _render_card_overlay(canvas, theme=theme, is_photo=False)
        if layout_mode == "icon" and img is not None:
            content_left, content_right = _composite_icon(
                canvas, img, has_alpha=has_alpha
            )
        else:
            content_left = CONTENT_LEFT
            content_right = CONTENT_RIGHT

    draw = ImageDraw.Draw(canvas)

    # 6. Draw header (badge, title, subtitle, divider)
    curr_y = _draw_header(
        draw,
        card_data,
        CARD_Y0 + 36,
        content_left=content_left,
        content_right=content_right,
        theme=theme,
    )

    # 7. Draw body content: bullet points
    raw_bullets = card_data.get("bullet_points")
    if isinstance(raw_bullets, list):
        _draw_bullets(
            draw,
            raw_bullets,
            curr_y,
            content_left=content_left,
            content_right=content_right,
            theme=theme,
        )

    # 8. Ensure output directory exists and save RGB JPEG or PNG
    output_path = Path(output_jpeg_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".png":
        canvas.save(str(output_path), "PNG")
    else:
        canvas.save(str(output_path), "JPEG", quality=95)
    return str(output_path)
