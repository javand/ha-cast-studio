"""Constants for automation_generator."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "automation_generator"
ATTRIBUTION = "Data provided by Home Assistant Automation Generator"

DEFAULT_OUTPUT_DIR = "/config/www/automations"
DEFAULT_HOST_VOICE = "Pulcherrima"
DEFAULT_SCRIPTURE_VOICE = "Enceladus"

CONF_API_KEY = "api_key"
CONF_RECIPIENT_NAME = "recipient_name"
CONF_PHONETIC_NAME = "phonetic_name"
CONF_OUTPUT_DIR = "output_dir"

DEFAULT_RECIPIENT_NAME = "Javan"
DEFAULT_PHONETIC_NAME = "Jay-ven"
