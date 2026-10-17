"""Constants for automation_generator."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "automation_generator"
ATTRIBUTION = "Data provided by Home Assistant Automation Generator"

DEFAULT_OUTPUT_DIR = "/config/www/automations"
DEFAULT_HOST_VOICE = "Pulcherrima"
DEFAULT_SCRIPTURE_VOICE = "Enceladus"
