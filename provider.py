"""Explicit provider selection; no automatic model/provider fallback."""

from __future__ import annotations

import time
from pathlib import Path

import openai

from .api_support import api_key, http_error, pace, redact_text, retry_wait, safe_value
from .models import ExperimentConfig
from .storage import utcnow, write_json, write_text


def request_payload(messages: list[dict], config: ExperimentConfig, repetition: int) -> dict:
    if config.provider == "gemini":
        from .gemini import request_payload as gemini_payload

        return gemini_payload(messages, config, repetition)
    payload = {
        "model": config.model,
        "messages": messages,
        "max_completion_tokens": config.max_completion_tokens,
        "response_format": {"type": "json_object"},
        "n": 1,
        "store": False,
    }
    if config.temperature is not None:
        payload["temperature"] = config.temperature
    if config.generation_seed is not None:
        payload["seed"] = config.generation_seed + repetition - 1
    if config.reasoning_effort is not None:
        payload["reasoning_effort"] = config.reasoning_effort
    return payload


def generate(messages: list[dict], config: ExperimentConfig, repetition: int, output: Path) -> dict:
    if config.provider == "gemini":
        from .gemini import generate as generate_gemini

        return generate_gemini(messages, config, repetition, output)
    key = api_key(config)
    output.mkdir(parents=True, exist_ok=False)
    payload = request_payload(messages, config, repetition)
    write_json(output / "request.json", safe_value(payload, key))
    calls, response = [], None
    client = openai.OpenAI(
        api_key=key,
        base_url="https://api.openai.com/v1",
        max_retries=0,
        timeout=config.llm_timeout_seconds,
    )
    try:
        for retry in range(config.technical_retries + 1):
            waited = pace(config)
            start, stamp = time.monotonic(), utcnow()
            error = None
            try:
                response = client.chat.completions.create(**payload)
            except (openai.APITimeoutError, openai.APIConnectionError) as exc:
                error = {
                    "type": type(exc).__name__,
                    "status_code": None,
                    "provider_message": redact_text(str(exc), key)[:5000],
                    "retryable": True,
                    "usage_unknown": True,
                    "completion_uncertain": True,
                }
            except openai.APIStatusError as exc:
                error = http_error(
                    exc.status_code,
                    exc.body,
                    exc.response.text,
                    exc.response.headers,
                    key,
                    type(exc).__name__,
                )
            item = {
                "retry_index": retry,
                "started_at": stamp,
                "finished_at": utcnow(),
                "duration_seconds": time.monotonic() - start,
                "pacing_wait_seconds": waited,
                "error": error,
                "provider_request_id": getattr(response, "_request_id", None)
                if response
                else error.get("request_id"),
            }
            calls.append(item)
            write_json(output / f"transport-{retry}.json", item)
            if (
                response is not None
                or retry == config.technical_retries
                or not retry_wait(error, config)
            ):
                break
    finally:
        client.close()
    if response is None:
        result = {
            "status": "technical_failure",
            "provider": "openai",
            "calls": calls,
            "text": None,
            "usage": None,
            "cost_usd_estimate": None,
            "cost_unavailable_reason": "No usable response; usage may have been incurred.",
        }
    else:
        envelope = safe_value(response.model_dump(mode="json"), key)
        write_json(output / "provider_response.json", envelope)
        choice = response.choices[0] if response.choices else None
        text = choice.message.content if choice else None
        text = text if isinstance(text, str) else ""
        write_text(output / "raw.txt", text)
        usage = envelope.get("usage")
        cost, reason = estimate_cost(usage, config, any(c["error"] for c in calls))
        result = {
            "status": "received",
            "provider": "openai",
            "calls": calls,
            "text": text,
            "response_id": response.id,
            "served_model": response.model,
            "system_fingerprint": envelope.get("system_fingerprint"),
            "finish_reason": choice.finish_reason if choice else None,
            "refusal": choice.message.refusal if choice else None,
            "usage": usage,
            "cost_usd_estimate": cost,
            "cost_unavailable_reason": reason,
        }
    write_json(output / "generation.json", result)
    return result


def estimate_cost(
    usage: dict | None, config: ExperimentConfig, uncertain_retry: bool
) -> tuple[float | None, str | None]:
    if uncertain_retry:
        return (
            None,
            "A failed transport attempt has unknown billable usage. Successful response usage remains recorded.",
        )
    if not usage:
        return None, "Provider usage unavailable."
    if config.input_usd_per_million is None or config.output_usd_per_million is None:
        return None, "Pricing has not been configured with source and date."
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    if type(cached) is not int or cached < 0:
        return None, "Cached token counter unavailable or invalid."
    if cached and config.cached_input_usd_per_million is None:
        return None, "Cached tokens reported but cached-token price unavailable."
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if type(prompt) is not int or type(completion) is not int or prompt < cached or completion < 0:
        return None, "Prompt/completion token counters unavailable."
    return (
        (prompt - cached) * config.input_usd_per_million
        + cached * (config.cached_input_usd_per_million or 0)
        + completion * config.output_usd_per_million
    ) / 1_000_000, None
