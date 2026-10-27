"""Image platform for cast_studio: each announcement's current slide."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components.image import ImageEntity

from .announcement_entity import (
    AnnouncementEntity,
    async_setup_announcement_platform,
)
from .helpers import async_run_in_executor
from .people import slugify_name
from .personas import get_persona

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .announcements import Announcement


class CastStudioAnnouncementImage(AnnouncementEntity, ImageEntity):
    """The slide an announcement shows, with its words as attributes."""

    _attr_name = None

    def __init__(self, hass: HomeAssistant, announcement: Announcement) -> None:
        """Initialise as ``image.cast_studio_announcement_<name>``."""
        AnnouncementEntity.__init__(self, announcement)
        ImageEntity.__init__(self, hass)
        self.entity_id = (
            f"image.cast_studio_announcement_{slugify_name(announcement.name)}"
        )
        self._attr_unique_id = (
            f"cast_studio_announcement_{announcement.announcement_id}"
        )
        self._handle_update()

    def _slide(self) -> Path | None:
        build = self.announcement_build()
        return Path(build.slide_path) if build and build.slide_path else None

    def _handle_update(self) -> None:
        build = self.announcement_build() if self.hass else None
        self._attr_image_last_updated = build.built_at if build else None
        if build is not None and build.slide_path:
            self._attr_content_type = (
                "image/png" if build.slide_path.endswith(".png") else "image/jpeg"
            )

    async def async_image(self) -> bytes | None:
        """Return the current slide, or None for audio announcements."""
        slide = self._slide()
        if slide is None:
            return None
        try:
            return await async_run_in_executor(self.hass, slide.read_bytes)
        except OSError:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the words, settings and build status."""
        state = self.announcement_state()
        build = state.build if state else None
        announcement = self.announcement()
        persona = get_persona(build.persona_id) if build else None
        return {
            "words": build.words if build else None,
            "wording": announcement.wording if announcement else None,
            "freshness": announcement.freshness if announcement else None,
            "persona": persona.name if persona else None,
            "built_at": build.built_at.isoformat() if build else None,
            "status": state.status if state else None,
            "error": state.error if state else None,
            "note": build.note if build else None,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a slide image for each announcement."""
    async_setup_announcement_platform(
        hass,
        entry,
        async_add_entities,
        lambda a: CastStudioAnnouncementImage(hass, a),
    )
