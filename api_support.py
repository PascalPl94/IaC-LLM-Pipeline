"""Credentials, redacted diagnostics and explicit transport pacing."""

from __future__ import annotations

import getpass
import json
import os
import re
import sys
import time
import warnings
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from .models import ExperimentConfig

_LAST_STARTED: dict[tuple[str, str], float] = {}


def key_environment(config: ExperimentConfig) -> str:
    return "GEMINI_API_KEY" if config.provider == "gemini" else "OPENAI_API_KEY"


def api_key(config: ExperimentConfig, *, interactive: bool = False, force_prompt: bool = False) -> str:
    name = key_environment(config)
    key = "" if force_prompt else os.environ.get(name, "").strip()

    def valid(value: str) -> bool:
        return (
            bool(value)
            and not any(c.isspace() for c in value)
            and value
            not in {
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "OPENAI_API_KEY",
            }
            and not value.startswith(("'", '"', "export"))
        )

    if not valid(key) and interactive and sys.stdin.isatty():
        label = "Gemini" if config.provider == "gemini" else "OpenAI"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                key = getpass.getpass(f"{label} API key (input hidden): ").strip()
        except (EOFError, getpass.GetPassWarning) as exc:
            raise ValueError("Hidden input unavailable. Use the Mac Terminal or set " + name) from exc
    if not valid(key):
        raise ValueError(f"Set {name}, or run from an interactive Terminal. No API call was made.")
    if config.provider == "gemini" and key.startswith("sk-"):
        raise ValueError("This looks like an OpenAI key. Use a Gemini API key from AI Studio.")
    os.environ[name] = key  # Only this Python process; never a shell-history entry.
    return key


def redact_text(text: str, key: str) -> str:
    text = text.replace(key, "[REDACTED]") if key else text
    return re.sub(r"sk-[A-Za-z0-9_.*-]+|AIza[A-Za-z0-9_-]{20,}", "[REDACTED]", text)


def safe_value(value, key: str):
    return json.loads(redact_text(json.dumps(value, ensure_ascii=False, default=str), key))


def objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)


def http_error(status: int, body, text: str, headers, key: str, error_type: str) -> dict:
    rows = list(objects(body))
    detail = next(
        (row for row in rows if isinstance(row.get("message"), str) and row["message"].strip()), {}
    )
    message = detail.get("message") or text.strip() or f"HTTP {status}; empty response body."
    diagnostic_text = json.dumps(body, default=str).lower()
    code = detail.get("code")
    quota_blocked = (
        detail.get("type") == "insufficient_quota"
        or str(code)
        in {
            "insufficient_quota",
            "credit_balance_exhausted",
            "organization_spend_limit_exceeded",
            "project_spend_limit_exceeded",
            "organization_usage_limit_exceeded",
        }
        or status == 429
        and (
            any(
                marker in diagnostic_text
                for marker in ("perday", "per_day", "per day", "daily quota", "daily limit")
            )
            or any(str(row.get("quotaValue")) == "0" for row in rows)
        )
    )
    delay = None
    value = headers.get("retry-after")
    if value:
        try:
            delay = max(0.0, float(value))
        except ValueError:
            try:
                delay = max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    for row in rows:
        retry_delay = row.get("retryDelay")
        if isinstance(retry_delay, str) and re.fullmatch(r"\d+(?:\.\d+)?s", retry_delay):
            delay = max(delay or 0.0, float(retry_delay[:-1]))
    retryable = (status in {408, 429} or status >= 500) and not quota_blocked
    return {
        "type": error_type,
        "status_code": status,
        "provider_message": redact_text(str(message), key)[:5000],
        "provider_code": safe_value(code, key),
        "provider_status": safe_value(detail.get("status") or detail.get("type"), key),
        "provider_param": safe_value(detail.get("param"), key),
        "provider_body": safe_value(body if body is not None else text, key),
        "request_id": headers.get("x-request-id") or headers.get("x-goog-request-id"),
        "retryable": retryable,
        "retry_after_seconds": delay,
        "quota_or_billing_blocked": quota_blocked,
        "usage_unknown": True,
        "completion_uncertain": status >= 500,
    }


def pace(config: ExperimentConfig) -> float:
    if config.min_request_interval_seconds == 0:
        return 0.0
    identity = (config.provider, config.model)
    start = time.monotonic()
    previous = _LAST_STARTED.get(identity)
    delay = (
        max(0.0, config.min_request_interval_seconds - (start - previous))
        if previous is not None
        else 0.0
    )
    if delay:
        time.sleep(delay)
    _LAST_STARTED[identity] = time.monotonic()
    return time.monotonic() - start


def retry_wait(error: dict, config: ExperimentConfig) -> bool:
    if not error.get("retryable"):
        return False
    delay = max(1.0, error.get("retry_after_seconds") or 0.0)
    if delay > config.max_retry_wait_seconds:
        return False
    time.sleep(delay)
    return True
