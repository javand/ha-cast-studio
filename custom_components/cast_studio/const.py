"""Constants for cast_studio."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "cast_studio"
ATTRIBUTION = "Data provided by CastStudio"

DEFAULT_OUTPUT_DIR = "/config/www/cast_studio"
DEFAULT_STATIC_DIR = "/config/www/cast_studio/static"
DEFAULT_HOST_VOICE = "Pulcherrima"
DEFAULT_SCRIPTURE_VOICE = "Enceladus"

CONF_API_KEY = "api_key"
CONF_RECIPIENT_NAME = "recipient_name"
CONF_PHONETIC_NAME = "phonetic_name"
CONF_OUTPUT_DIR = "output_dir"
CONF_DEFAULT_TRANSPORTATION_MODE = "default_transportation_mode"
CONF_MAPS_API_KEY = "maps_api_key"
CONF_COMMUTE_DESTINATION = "commute_destination_zone"

DEFAULT_RECIPIENT_NAME = "Javan"
DEFAULT_PHONETIC_NAME = "Jay-ven"
DEFAULT_TRANSPORTATION_MODE = "fastest"
