"""Immutable attempts, last-output selection, bounded repair and common assessment."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from .evaluator import evaluate
from .models import RESOURCE_TYPES, ExperimentConfig, GeneratedOutput, Scenario
from .provider import generate
from .storage import canonical, digest, event, load, loads, utcnow, write_json, write_text


def initial_messages(scenario: Scenario, root: Path, config: ExperimentConfig) -> list[dict]:
    user = {
        "scenario": scenario.model_dump(),
        "allowed_resources": list(RESOURCE_TYPES),
        "terraform_version": config.terraform_version,
        "azurerm_version": config.azurerm_version,
        "response_contract": GeneratedOutput.model_json_schema(),
    }
    return [
        {"role": "system", "content": (root / "frozen/prompts/system.txt").read_text()},
        {"role": "user", "content": canonical(user)},
    ]


def retain_output(generation: dict, attempt: Path) -> tuple[Path | None, list[dict]]:
    if generation["status"] != "received":
        return None, [
            {
                "rule_id": "API_TECHNICAL_FAILURE",
                "outcome": "unresolved",
                "message": "No model output was received.",
            }
        ]
    errors = []
    if generation.get("finish_reason") != "stop" or generation.get("refusal"):
        errors.append(
            {
                "rule_id": "OUTPUT_COMPLETION",
                "outcome": "fail",
                "message": "Response incomplete or refused.",
                "finish_reason": generation.get("finish_reason"),
            }
        )
    try:
        decoded = loads(generation["text"])
        output = GeneratedOutput.model_validate(decoded)
        write_json(attempt / "parsed_response.json", output.model_dump())
    except (ValueError, TypeError, ValidationError) as exc:
        return None, [
            *errors,
            {"rule_id": "OUTPUT_CONTRACT", "outcome": "fail", "message": str(exc)[:4000]},
        ]
    if output.status != "ok":
        return None, [
            *errors,
            {
                "rule_id": "OUTPUT_UNSUPPORTED",
                "outcome": "fail",
                "message": "Supported benchmark scenario was not implemented.",
            },
        ]
    directory = attempt / "terraform"
    directory.mkdir(exist_ok=False)
    hashes = {}
    for file in output.terraform_files:
        write_text(directory / file.path, file.content)
        hashes[file.path] = digest(file.content.encode("utf-8"))
    write_json(attempt / "bundle_hashes.json", hashes)
    return directory, errors


def run_case(
    row: dict,
    root: Path,
    config: ExperimentConfig,
    generate_fn: Callable = generate,
    evaluate_fn: Callable = evaluate,
) -> dict:
    case_id, condition = row["case_id"], row["condition"]
    case = root / "cases" / case_id
    case.mkdir(parents=True, exist_ok=False)
    scenario = Scenario.model_validate(load(root / "frozen/inputs" / f"{row['scenario_id']}.json"))
    # Main experiment inputs are pre-validated at freeze. Baseline receives no experimental domain feedback.
    if condition == "guardrailed":
        scenario.assert_supported()
    write_json(case / "input/raw.json", load(root / "frozen/inputs" / f"{row['scenario_id']}.json"))
    write_json(case / "input/normalized.json", scenario.model_dump())
    messages = initial_messages(scenario, root, config)
    write_json(case / "initial_prompt_hash.json", {"sha256": digest(canonical(messages).encode())})
    start, stamp = time.monotonic(), utcnow()
    event(root, "case_started", **row)
    attempts, final_bundle, final_errors = [], None, []
    budget = config.max_repairs if condition == "guardrailed" else 0
    lock = root / "frozen/provider.lock.hcl"
    initial_bundle, initial_errors = None, []
    technical = False
    for index in range(budget + 1):
        attempt = case / "attempts" / str(index)
        attempt.mkdir(parents=True, exist_ok=False)
        write_json(attempt / "messages.json", messages)
        generation = generate_fn(messages, config, int(row["repetition"]), attempt / "response")
        final_bundle, final_errors = retain_output(generation, attempt)
        if index == 0:
            initial_bundle, initial_errors = final_bundle, final_errors
        summary = {
            "index": index,
            "generation_status": generation["status"],
            "transport_calls": len(generation["calls"]),
            "usage": generation.get("usage"),
            "cost_usd_estimate": generation.get("cost_usd_estimate"),
            "cost_unavailable_reason": generation.get("cost_unavailable_reason"),
            "llm_duration_seconds": sum(c["duration_seconds"] for c in generation["calls"]),
            "pacing_wait_seconds": sum(c.get("pacing_wait_seconds", 0.0) for c in generation["calls"]),
        }
        attempts.append(summary)
        event(root, "attempt_retained", case_id=case_id, attempt=index, status=generation["status"])
        if generation["status"] == "technical_failure":
            technical = True
            break
        if condition == "baseline":
            break
        guard = evaluate_fn(
            scenario, final_bundle, attempt / "guardrails", config, root, final_errors, lock
        )
        write_json(
            attempt / "guardrail_summary.json",
            {"strict_success": guard["strict_success"], "technical_blocker": guard["technical_blocker"]},
        )
        if guard["strict_success"] or index == budget:
            break
        if guard["technical_blocker"]:
            technical = True
            break  # tool outages do not spend model repairs
        repair = (root / "frozen/prompts/repair.txt").read_text().format(repair_number=index + 1)
        assistant = {"role": "assistant", "content": generation["text"]}
        if config.provider == "gemini" and generation.get("assistant_parts") is not None:
            assistant["gemini_parts"] = generation["assistant_parts"]
        messages = [
            *messages,
            assistant,
            {"role": "user", "content": repair + "\n" + canonical({"findings": guard["feedback"]})},
        ]
    workflow_duration = time.monotonic() - start
    final_index = len(attempts) - 1
    pointer = {
        "attempt": final_index,
        "terraform": f"attempts/{final_index}/terraform" if final_bundle else None,
        "selection_rule": "last_retained_attempt",
        "workflow_finished_at": utcnow(),
    }
    write_json(case / "final.json", pointer)
    # Baseline's first evaluation occurs only AFTER workflow termination. Its results never re-enter generation.
    assessment = evaluate_fn(
        scenario, final_bundle, case / "evaluation/final", config, root, final_errors, lock
    )
    initial = None
    if condition == "guardrailed":
        if final_index == 0:
            initial = assessment
            write_json(
                case / "evaluation/initial_reuse.json",
                {"same_artifact_as_final": True, "assessment": "final/assessment.json"},
            )
        else:
            initial = evaluate_fn(
                scenario, initial_bundle, case / "evaluation/initial", config, root, initial_errors, lock
            )
    result = {
        **row,
        "data_origin": "real_llm",
        "started_at": stamp,
        "finished_at": utcnow(),
        "terminal_status": "success"
        if assessment["strict_success"]
        else (
            "technical_failure" if technical or assessment["technical_blocker"] else "quality_failure"
        ),
        "workflow_technical_blocker": technical,
        "workflow_duration_seconds": workflow_duration,
        "pacing_wait_seconds": sum(a["pacing_wait_seconds"] for a in attempts),
        "common_evaluation_duration_seconds": assessment["duration_seconds"]
        + (initial["duration_seconds"] if initial and final_index > 0 else 0),
        "repair_count": final_index,
        "attempts": attempts,
        "final_attempt": final_index,
        "strict_success": assessment["strict_success"],
        "intent_score": assessment["intent"]["score"],
        "initial_strict_success": initial["strict_success"] if initial else None,
        "initial_intent_score": initial["intent"]["score"] if initial else None,
        "assessment": "evaluation/final/assessment.json",
    }
    write_json(case / "result.json", result)
    event(root, "case_completed", case_id=case_id, terminal_status=result["terminal_status"])
    return result
