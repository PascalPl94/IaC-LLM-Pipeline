"""A real, separate API smoke check; never a benchmark observation."""

from __future__ import annotations

from pathlib import Path

from .models import ExperimentConfig
from .provider import generate
from .storage import loads, write_json


def check_api(config: ExperimentConfig, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "config.json", config.model_dump())
    generation = generate(
        [
            {"role": "system", "content": "Return only a JSON object. Do not add explanations."},
            {"role": "user", "content": 'Return exactly {"ok": true} as JSON.'},
        ],
        config,
        1,
        output / "response",
    )
    parsed, parse_error = None, None
    if generation["status"] == "received":
        try:
            parsed = loads(generation["text"])
        except (TypeError, ValueError) as exc:
            parse_error = str(exc)
    ready = (
        isinstance(parsed, dict)
        and set(parsed) == {"ok"}
        and parsed["ok"] is True
        and generation.get("finish_reason") == "stop"
        and not generation.get("refusal")
    )
    report = {
        "data_origin": "api_check_only",
        "api_ready": ready,
        "provider": config.provider,
        "requested_model": config.model,
        "served_model": generation.get("served_model"),
        "generation_status": generation["status"],
        "finish_reason": generation.get("finish_reason"),
        "refusal": generation.get("refusal"),
        "response_preview": (generation.get("text") or "")[:300],
        "errors": [call["error"] for call in generation["calls"] if call.get("error")],
        "parse_error": parse_error,
        "usage": generation.get("usage"),
        "cost_usd_estimate": generation.get("cost_usd_estimate"),
        "cost_unavailable_reason": generation.get("cost_unavailable_reason"),
        "transport_attempts": len(generation["calls"]),
        "collection_budget": {
            "pilot_cases": 4,
            "main_cases": 48,
            "minimum_model_calls_including_this_check": 53,
            "maximum_model_calls_including_this_check": 105,
            "maximum_transport_attempts_with_one_retry_each": 210,
        },
        "quota_note": "This checks one response, not remaining quota or student/Cloud-credit eligibility.",
    }
    write_json(output / "api_check.json", report)
    return report
