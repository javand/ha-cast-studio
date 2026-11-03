"""Constants for cast_studio."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "cast_studio"

DEFAULT_OUTPUT_DIR = "/config/www/cast_studio"
DEFAULT_HOST_VOICE = "Pulcherrima"
DEFAULT_SCRIPTURE_VOICE = "Enceladus"

CONF_API_KEY = "api_key"
CONF_RECIPIENT_NAME = "recipient_name"
CONF_PHONETIC_NAME = "phonetic_name"
CONF_OUTPUT_DIR = "output_dir"
CONF_DEFAULT_TRANSPORTATION_MODE = "default_transportation_mode"
CONF_MAPS_API_KEY = "maps_api_key"
CONF_COMMUTE_DESTINATION = "commute_destination_zone"
# Gemini models, one per job; a typed name works for models not listed.
CONF_TEXT_MODEL = "text_model"
CONF_IMAGE_MODEL = "image_model"
CONF_VOICE_MODEL = "voice_model"
DEFAULT_TEXT_MODEL = "gemini-2.5-flash"
DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"
DEFAULT_VOICE_MODEL = "gemini-3.8-flash-lite-tts"
TEXT_MODELS = ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite")
IMAGE_MODELS = ("gemini-2.5-flash-image",)
VOICE_MODELS = (
    "gemini-3.8-flash-lite-tts",
    "gemini-3.8-flash-tts",
    "gemini-2.5-flash-preview-tts",
)

# Only the v1 → v2 migration's fallbacks; the setup form pre-fills neither.
DEFAULT_RECIPIENT_NAME = "Friend"
DEFAULT_PHONETIC_NAME = "Friend"
DEFAULT_TRANSPORTATION_MODE = "fastest"
