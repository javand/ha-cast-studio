"""
Track AI provider account problems so the user can see and fix them.

A billing, key or quota rejection from Gemini or OpenAI silently degrades a
briefing to the fallback voice and text. This module records the rejection,
exposes it as the ``ai_account_issue`` attribute on every CastStudio sensor,
and raises a Repairs issue. The next successful call clears both.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DATA_AI_ACCOUNT_ISSUE = "ai_account_issue"
SIGNAL_AI_ACCOUNT_ISSUE = f"{DOMAIN}_ai_account_issue"
ISSUE_ID = "ai_account"
MESSAGE_LIMIT = 300

# HTTP statuses that mean the account (not the request) needs attention.
ACCOUNT_PROBLEMS: dict[int, str] = {
    HTTPStatus.UNAUTHORIZED: "invalid_api_key",
    HTTPStatus.PAYMENT_REQUIRED: "billing",
    HTTPStatus.FORBIDDEN: "permission_denied",
    HTTPStatus.TOO_MANY_REQUESTS: "quota_exceeded",
}

ACCOUNT_URLS = {
    "Gemini": "https://aistudio.google.com/",
    "OpenAI": "https://platform.openai.com/account/billing",
}


def account_problem(status: int | None) -> str | None:
    """Return the problem kind for an HTTP status, or None if not account-level."""
    if status is None:
        return None
    return ACCOUNT_PROBLEMS.get(status)


def _provider_message(body: str) -> str:
    """Pull the provider's human-readable message out of an error body."""
    message = body
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        pass
    else:
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                message = error["message"]
    return " ".join(message.split())[:MESSAGE_LIMIT]


PROBLEM_LABELS = {
    "invalid_api_key": "API key rejected",
    "billing": "billing problem",
    "permission_denied": "access denied",
    "quota_exceeded": "quota exceeded",
}


def _issues(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Return the open account problems, keyed by service (text/voice/image)."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(DATA_AI_ACCOUNT_ISSUE, {})


def get_ai_account_issue(hass: HomeAssistant) -> str | None:
    """
    Describe the open account problem for the sensors' attribute.

    For example "Gemini billing problem (text, voice, image): Your prepayment
    credits are depleted." None means the account is healthy.
    """
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return None
    issues = domain_data.get(DATA_AI_ACCOUNT_ISSUE) or {}
    if not issues:
        return None
    latest = max(issues.values(), key=lambda issue: issue["since"])
    services = ", ".join(issues)
    label = PROBLEM_LABELS[latest["problem"]]
    text = f"{latest['provider']} {label} ({services})"
    if latest["message"]:
        text += f": {latest['message']}"
    return text


def async_report_ai_failure(
    hass: HomeAssistant,
    provider: str,
    service: str,
    status: int | None,
    body: str = "",
) -> None:
    """Record a provider rejection when it points at the account."""
    problem = account_problem(status)
    if problem is None:
        return
    message = _provider_message(body)
    issues = _issues(hass)
    if not issues:
        LOGGER.error(
            "%s rejected CastStudio's %s request (%s, HTTP %s): %s",
            provider,
            service,
            problem,
            status,
            message,
        )
    issues[service] = {
        "problem": problem,
        "provider": provider,
        "message": message,
        "since": datetime.now(UTC).isoformat(),
    }
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ID,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=problem,
        translation_placeholders={
            "provider": provider,
            "service": service,
            "message": message or "no details given",
        },
        learn_more_url=ACCOUNT_URLS.get(provider),
    )
    async_dispatcher_send(hass, SIGNAL_AI_ACCOUNT_ISSUE)


def async_report_ai_success(hass: HomeAssistant, service: str) -> None:
    """Clear a service's account problem after one of its calls succeeds."""
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return
    issues = domain_data.get(DATA_AI_ACCOUNT_ISSUE) or {}
    if issues.pop(service, None) is None:
        return
    if not issues:
        LOGGER.info("AI provider account problem resolved")
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ID)
    async_dispatcher_send(hass, SIGNAL_AI_ACCOUNT_ISSUE)
