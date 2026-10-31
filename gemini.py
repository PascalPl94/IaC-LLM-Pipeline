"""Native Gemini generateContent API; one candidate, no model fallback."""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from .api_support import api_key, http_error, pace, redact_text, retry_wait, safe_value
from .models import ExperimentConfig
from .storage import utcnow, write_json, write_text

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/"


def request_payload(messages: list[dict], config: ExperimentConfig, repetition: int) -> dict:
    system, contents = [], []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if not isinstance(content, str) or role not in {"system", "user", "assistant"}:
            raise ValueError("Gemini adapter accepts text system/user/assistant messages only.")
        part = {"text": content}
        if role == "system":
            system.append(part)
        else:
            parts = message.get("gemini_parts") if role == "assistant" else None
            if parts is not None and (
                not isinstance(parts, list) or any(not isinstance(p, dict) for p in parts)
            ):
                raise ValueError("Gemini assistant history must preserve the original parts array.")
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": parts if parts is not None else [part],
                }
            )
    generation = {
        "maxOutputTokens": config.max_completion_tokens,
        "candidateCount": 1,
        "responseMimeType": "application/json",
    }
    if config.temperature is not None:
        generation["temperature"] = config.temperature
    if config.generation_seed is not None:
        generation["seed"] = config.generation_seed + repetition - 1
    if config.reasoning_effort is not None:
        if config.model.startswith(("gemini-3", "gemini-4")):
            generation["thinkingConfig"] = {"thinkingLevel": config.reasoning_effort.upper()}
        else:
            budgets = {"none": 0, "minimal": 1024, "low": 1024, "medium": 8192, "high": 24576}
            generation["thinkingConfig"] = {"thinkingBudget": budgets[config.reasoning_effort]}
    result = {"contents": contents, "generationConfig": generation}
    if system:
        result["systemInstruction"] = {"parts": system}
    return result


def normalize_response(envelope: dict, config: ExperimentConfig) -> dict:
    candidates = envelope.get("candidates") or []
    feedback = envelope.get("promptFeedback") or {}
    if not isinstance(candidates, list) or not candidates and not feedback.get("blockReason"):
        raise ValueError("Gemini returned neither candidates nor an explicit prompt-block reason.")
    candidate = candidates[0] if candidates else {}
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought")
    )
    finish = candidate.get("finishReason")
    blocked = feedback.get("blockReason")
    refusal = blocked or (finish if finish not in {None, "STOP", "MAX_TOKENS"} else None)
    native_usage = envelope.get("usageMetadata")
    usage = None
    if isinstance(native_usage, dict):

        def counter(name, default=None):
            value = native_usage.get(name, default)
            return value if type(value) is int and value >= 0 else None

        prompt = counter("promptTokenCount")
        candidates_count = counter("candidatesTokenCount")
        thoughts = counter("thoughtsTokenCount", 0)
        completion = (
            candidates_count + thoughts
            if candidates_count is not None and thoughts is not None
            else None
        )
        usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": counter("totalTokenCount"),
            "prompt_tokens_details": {"cached_tokens": counter("cachedContentTokenCount", 0)},
            "completion_tokens_details": {
                "reasoning_tokens": thoughts,
                "visible_output_tokens": candidates_count,
            },
            "provider_usage": native_usage,
        }
    return {
        "text": text,
        "assistant_parts": parts,
        "response_id": envelope.get("responseId"),
        "served_model": envelope.get("modelVersion"),
        "requested_model": config.model,
        "system_fingerprint": None,
        "finish_reason": "stop" if finish == "STOP" else "length" if finish == "MAX_TOKENS" else finish,
        "provider_finish_reason": finish,
        "refusal": refusal,
        "usage": usage,
    }


def generate(messages: list[dict], config: ExperimentConfig, repetition: int, output: Path) -> dict:
    from .provider import estimate_cost

    key = api_key(config)
    payload = request_payload(messages, config, repetition)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "request.json", safe_value(payload, key))
    write_json(
        output / "endpoint.json",
        {"provider": "gemini", "method": "POST", "url": BASE_URL + config.model + ":generateContent"},
    )
    calls, envelope, normalized = [], None, None
    with httpx.Client(timeout=config.llm_timeout_seconds, follow_redirects=False) as client:
        for retry in range(config.technical_retries + 1):
            waited = pace(config)
            start, stamp = time.monotonic(), utcnow()
            error, request_id = None, None
            try:
                response = client.post(
                    BASE_URL + config.model + ":generateContent",
                    headers={"x-goog-api-key": key},
                    json=payload,
                )
                request_id = response.headers.get("x-request-id") or response.headers.get(
                    "x-goog-request-id"
                )
                try:
                    body = response.json()
                except ValueError:
                    body = response.text
                if not response.is_success:
                    error = http_error(
                        response.status_code,
                        body,
                        response.text,
                        response.headers,
                        key,
                        "GeminiAPIError",
                    )
                else:
                    try:
                        if not isinstance(body, dict):
                            raise ValueError("Gemini returned a non-object HTTP success response.")
                        normalized = normalize_response(body, config)
                        envelope = body
                    except (ValueError, TypeError, KeyError, AttributeError) as exc:
                        error = http_error(
                            response.status_code,
                            body,
                            str(exc),
                            response.headers,
                            key,
                            "InvalidProviderResponse",
                        )
                        error["retryable"] = False
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                error = {
                    "type": type(exc).__name__,
                    "status_code": None,
                    "provider_message": redact_text(str(exc), key)[:5000],
                    "retryable": True,
                    "usage_unknown": True,
                    "completion_uncertain": True,
                }
            item = {
                "retry_index": retry,
                "started_at": stamp,
                "finished_at": utcnow(),
                "duration_seconds": time.monotonic() - start,
                "pacing_wait_seconds": waited,
                "error": error,
                "provider_request_id": request_id,
            }
            calls.append(item)
            write_json(output / f"transport-{retry}.json", item)
            if (
                normalized is not None
                or retry == config.technical_retries
                or not retry_wait(error, config)
            ):
                break
    if normalized is None:
        result = {
            "status": "technical_failure",
            "provider": "gemini",
            "calls": calls,
            "text": None,
            "usage": None,
            "cost_usd_estimate": None,
            "cost_unavailable_reason": "No usable response; failed-call usage is unknown.",
        }
    else:
        write_json(output / "provider_response.json", safe_value(envelope, key))
        normalized = safe_value(normalized, key)
        write_text(output / "raw.txt", normalized["text"])
        cost, reason = estimate_cost(normalized["usage"], config, any(c["error"] for c in calls))
        result = {
            "status": "received",
            "provider": "gemini",
            "calls": calls,
            **normalized,
            "cost_usd_estimate": cost,
            "cost_unavailable_reason": reason,
        }
    write_json(output / "generation.json", result)
    return result
