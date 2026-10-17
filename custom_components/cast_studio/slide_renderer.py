"""
Pillow Slide Visual Renderer (Real Name Enforced).

Renders high-contrast 1024x600 pixels (16:9 aspect ratio) RGB JPEG slides
for smart displays with dark gradients, translucent rounded cards, and typography.
Enforces the identity contract by asserting that phonetic spellings never leak onto
visual slides.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# Display dimensions
CANVAS_WIDTH = 1024
CANVAS_HEIGHT = 600

# Color palette (high-contrast dark theme)
BG_COLOR_TOP = (15, 23, 42)  # Slate-900
BG_COLOR_BOTTOM = (2, 6, 23)  # Slate-950
CARD_BG_COLOR = (30, 41, 59, 215)  # Slate-800 with ~84% opacity
CARD_BORDER_COLOR = (71, 85, 105, 200)  # Slate-600
TITLE_COLOR = (255, 255, 255)  # Crisp white
SUBTITLE_COLOR = (56, 189, 248)  # Sky-400 accent
DIVIDER_COLOR = (51, 65, 85, 180)  # Slate-700
BULLET_TEXT_COLOR = (241, 245, 249)  # Slate-100
BULLET_DOT_COLOR = (56, 189, 248)  # Sky-400 accent

# Card bounding layout
CARD_X0 = 60
CARD_Y0 = 45
CARD_X1 = 964
CARD_Y1 = 555
CARD_RADIUS = 24

CONTENT_LEFT = CARD_X0 + 44
CONTENT_RIGHT = CARD_X1 - 44
MAX_TEXT_WIDTH = CONTENT_RIGHT - CONTENT_LEFT


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


def _render_gradient_background() -> Image.Image:
    """Render background canvas with dark vertical gradient."""
    canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT))
    grad_draw = ImageDraw.Draw(canvas)
    for y in range(CANVAS_HEIGHT):
        ratio = y / CANVAS_HEIGHT
        r = int(BG_COLOR_TOP[0] * (1.0 - ratio) + BG_COLOR_BOTTOM[0] * ratio)
        g = int(BG_COLOR_TOP[1] * (1.0 - ratio) + BG_COLOR_BOTTOM[1] * ratio)
        b = int(BG_COLOR_TOP[2] * (1.0 - ratio) + BG_COLOR_BOTTOM[2] * ratio)
        grad_draw.line([(0, y), (CANVAS_WIDTH, y)], fill=(r, g, b))
    return canvas


def _render_card_overlay(canvas: Image.Image) -> Image.Image:
    """Composite translucent rounded card overlay on top of canvas."""
    overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        (CARD_X0, CARD_Y0, CARD_X1, CARD_Y1),
        radius=CARD_RADIUS,
        fill=CARD_BG_COLOR,
        outline=CARD_BORDER_COLOR,
        width=1,
    )
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def _draw_header(
    draw: ImageDraw.ImageDraw,
    card_data: dict[str, Any],
    start_y: int,
) -> int:
    """Draw title, subtitle, and divider line, returning updated y coordinate."""
    title_font = _get_font(36, bold=True)
    subtitle_font = _get_font(22, bold=False)
    curr_y = start_y

    title = str(card_data.get("title", "")).strip()
    if title:
        title_lines = _wrap_text(title, title_font, MAX_TEXT_WIDTH, draw)
        for line in title_lines:
            bbox = draw.textbbox((CONTENT_LEFT, curr_y), line, font=title_font)
            line_height = bbox[3] - bbox[1]
            draw.text((CONTENT_LEFT, curr_y), line, font=title_font, fill=TITLE_COLOR)
            curr_y += line_height + 6

    subtitle = str(card_data.get("subtitle", "")).strip()
    if subtitle:
        curr_y += 4
        sub_lines = _wrap_text(subtitle, subtitle_font, MAX_TEXT_WIDTH, draw)
        for line in sub_lines:
            bbox = draw.textbbox((CONTENT_LEFT, curr_y), line, font=subtitle_font)
            line_height = bbox[3] - bbox[1]
            draw.text(
                (CONTENT_LEFT, curr_y), line, font=subtitle_font, fill=SUBTITLE_COLOR
            )
            curr_y += line_height + 4

    if title or subtitle:
        curr_y += 12
        draw.line(
            [(CONTENT_LEFT, curr_y), (CONTENT_RIGHT, curr_y)],
            fill=DIVIDER_COLOR,
            width=1,
        )
        curr_y += 20

    return curr_y


def _draw_bullets(
    draw: ImageDraw.ImageDraw,
    raw_bullets: list[Any],
    start_y: int,
) -> int:
    """Draw bullet points with dot indicators and text wrapping."""
    bullet_font = _get_font(24, bold=False)
    bullet_indent = CONTENT_LEFT + 28
    bullet_max_width = CONTENT_RIGHT - bullet_indent
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
        dot_x = CONTENT_LEFT + 8
        draw.ellipse(
            [
                (dot_x - dot_radius, dot_center_y - dot_radius),
                (dot_x + dot_radius, dot_center_y + dot_radius),
            ],
            fill=BULLET_DOT_COLOR,
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


def render_slide(
    card_data: dict[str, Any],
    output_jpeg_path: str,
    phonetic_name: str | None = None,
) -> str:
    """Render a structured visual card to a 1024x600 high-contrast RGB JPEG."""
    if not isinstance(card_data, dict):
        msg = "card_data must be a dict"
        raise InvalidCardDataError(msg)

    # 1. Enforce identity contract before generating visual pixels
    _assert_identity_contract(card_data, phonetic_name=phonetic_name)

    # 2. Render background and translucent card
    canvas = _render_gradient_background()
    canvas = _render_card_overlay(canvas)
    draw = ImageDraw.Draw(canvas)

    # 3. Draw header (title, subtitle, divider)
    curr_y = _draw_header(draw, card_data, CARD_Y0 + 36)

    # 4. Draw bullet points
    raw_bullets = card_data.get("bullet_points")
    if isinstance(raw_bullets, list):
        _draw_bullets(draw, raw_bullets, curr_y)

    # 5. Ensure output directory exists and save RGB JPEG
    output_path = Path(output_jpeg_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(output_path), "JPEG", quality=95)
    return str(output_path)
