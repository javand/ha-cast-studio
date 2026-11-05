"""Tests for AI-assisted announcement layout planning."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.cast_studio.announcement_layout import (
    async_plan_announcement_layout,
    extract_referenced_entities,
)

CREDS = {"platform": "gemini", "api_key": "fake_key"}


def test_extract_referenced_entities(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.nba_golden_state_warriors", "PRE")
    hass.states.async_set("weather.home", "sunny")

    prompt = (
        "Show gameday alert from sensor.nba_golden_state_warriors "
        "and weather from weather.home on the right"
    )
    entities = extract_referenced_entities(prompt, hass)
    assert "sensor.nba_golden_state_warriors" in entities
    assert "weather.home" in entities
    assert "light.non_existent" not in entities


@pytest.mark.asyncio
async def test_plan_announcement_layout_returns_validated_spec(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "sensor.nba_golden_state_warriors",
        "PRE",
        {
            "opponent_long_name": "Miami Heat",
            "clock": "11/5 - 10:00 PM EST",
            "team_long_name": "Golden State Warriors",
        },
    )

    mock_response = {
        "elements": [
            {
                "role": "badge",
                "template": "Upcoming Game",
                "box": [400, 80, 200, 40],
                "font_size": 18,
                "font_weight": "bold",
                "color": "#ffffff",
                "badge": True,
            },
            {
                "role": "title",
                "template": "Golden State Warriors",
                "box": [400, 140, 560, 80],
                "font_size": 52,
                "font_weight": "bold",
                "color": "#ffffff",
            },
            {
                "role": "subtitle",
                "template": "vs. {{ state_attr('sensor.nba_golden_state_warriors', 'opponent_long_name') }}",
                "box": [400, 230, 560, 50],
                "font_size": 32,
                "font_weight": "regular",
                "color": "#f0f0f0",
            },
            {
                "role": "footer",
                "template": "{{ state_attr('sensor.nba_golden_state_warriors', 'clock') }}",
                "box": [400, 290, 560, 40],
                "font_size": 22,
                "font_weight": "regular",
                "color": "#cccccc",
                "style": "italic",
            },
        ]
    }

    with patch(
        "custom_components.cast_studio.announcement_layout._call_ai_multimodal_json",
        new=AsyncMock(return_value=mock_response),
    ):
        result = await async_plan_announcement_layout(
            hass=hass,
            image_bytes=b"pngdata",
            image_mime="image/png",
            prompt="Show gameday alert from sensor.nba_golden_state_warriors on the right",
            entity_ids=None,
            ai_creds=CREDS,
        )

        assert "elements" in result
        assert len(result["elements"]) == 4
        assert result["elements"][1]["template"] == "Golden State Warriors"
        assert "{{ state_attr(" in result["elements"][2]["template"]
