"""Descriptive reports with all scheduled cases in success denominators."""

from __future__ import annotations

import csv
import io
import statistics
from collections import Counter
from pathlib import Path

from .storage import canonical, load, write_json, write_text


def csv_text(rows: list[dict], fields: list[str] | None = None) -> str:
    if not rows:
        return ""
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields or list(rows[0]), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def analyse(root: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    manifest = load(root / "experiment_manifest.json")
    schedule = load(root / "frozen/schedule.json")
    cases, requirements, policies, errors, available = [], [], [], Counter(), {}
    for row in schedule:
        case = root / "cases" / row["case_id"]
        result = load(case / "result.json") if (case / "result.json").exists() else {}
        if result:
            available[row["case_id"]] = result
        assessment = (
            load(case / "evaluation/final/assessment.json")
            if (case / "evaluation/final/assessment.json").exists()
            else {}
        )
        intent = assessment.get("intent", {})
        attempts = result.get("attempts", [])
        usages = [a.get("usage") for a in attempts]
        costs = [a.get("cost_usd_estimate") for a in attempts]
        token_complete = (
            bool(usages)
            and all(
                u is not None and type(u.get("total_tokens")) is int and u["total_tokens"] >= 0
                for u in usages
            )
            and all(a.get("transport_calls", 0) == 1 for a in attempts)
        )
        observed_counts = [
            u["total_tokens"]
            for u in usages
            if u and type(u.get("total_tokens")) is int and u["total_tokens"] >= 0
        ]
        tokens = sum(observed_counts) if observed_counts else None
        completed = bool(result) and result.get("terminal_status") != "interrupted"
        scored = result.get("intent_score") if completed else None
        cases.append(
            {
                **row,
                "terminal_status": result.get("terminal_status", "pending"),
                "data_origin": result.get("data_origin"),
                "strict_success": bool(result.get("strict_success", False)),
                "intent_score": scored,
                "intent_passed": intent.get("passed"),
                "intent_applicable": intent.get("applicable"),
                "validation_status": assessment.get("validation", {}).get("status", "not_run"),
                "validation_valid": assessment.get("validation", {}).get("valid"),
                "policy_coverage_complete": assessment.get("policy", {}).get("coverage_complete", False),
                "repair_count": result.get("repair_count"),
                "workflow_seconds": result.get("workflow_duration_seconds"),
                "pacing_wait_seconds": result.get("pacing_wait_seconds"),
                "llm_transport_seconds": sum(a.get("llm_duration_seconds", 0.0) for a in attempts)
                if attempts
                else None,
                "common_evaluation_seconds": result.get("common_evaluation_duration_seconds"),
                "observed_tokens": tokens,
                "token_usage_complete": token_complete,
                "cost_usd_estimate": sum(costs) if costs and all(c is not None for c in costs) else None,
                "initial_strict_success": result.get("initial_strict_success"),
                "initial_intent_score": result.get("initial_intent_score"),
            }
        )
        for req in intent.get("requirements", []):
            requirements.append(
                {
                    **row,
                    "requirement_id": req["requirement_id"],
                    "category": req["category"],
                    "outcome": req["outcome"],
                    "expected": canonical(req["expected"]).strip(),
                    "actual": canonical(req["actual"]).strip(),
                    "evidence": ";".join(req["evidence"]),
                }
            )
            if req["outcome"] != "pass":
                errors[(row["condition"], req["category"], req["outcome"])] += 1
        for instance in assessment.get("policy", {}).get("instances", []):
            policies.append({**row, **instance})
        for finding in assessment.get("output_contract", {}).get("findings", []):
            errors[(row["condition"], "output_contract", finding.get("outcome", "fail"))] += 1
    scenario_rows = []
    for sid in sorted({r["scenario_id"] for r in schedule}):
        for condition in ["baseline", "guardrailed"]:
            group = [r for r in cases if r["scenario_id"] == sid and r["condition"] == condition]
            scores = [r["intent_score"] for r in group if r["intent_score"] is not None]
            scenario_rows.append(
                {
                    "scenario_id": sid,
                    "condition": condition,
                    "scheduled": len(group),
                    "completed": sum(
                        r["terminal_status"] not in {"pending", "interrupted"} for r in group
                    ),
                    "successes": sum(r["strict_success"] for r in group),
                    "strict_success_rate": sum(r["strict_success"] for r in group) / len(group),
                    "requirement_score_mean_completed": statistics.mean(scores) if scores else None,
                    "requirement_score_mean_all_scheduled": sum(scores) / len(group),
                }
            )
    summaries = []
    for condition in ["baseline", "guardrailed"]:
        group = [r for r in cases if r["condition"] == condition]
        scenarios = [r for r in scenario_rows if r["condition"] == condition]
        validated = [r for r in group if r["validation_status"] == "completed"]
        times = [r["workflow_seconds"] for r in group if r["workflow_seconds"] is not None]
        summaries.append(
            {
                "condition": condition,
                "scheduled": len(group),
                "retained_cases": sum(r["terminal_status"] != "pending" for r in group),
                "pending": sum(r["terminal_status"] == "pending" for r in group),
                "interrupted": sum(r["terminal_status"] == "interrupted" for r in group),
                "technical_failures": sum(r["terminal_status"] == "technical_failure" for r in group),
                "successes": sum(r["strict_success"] for r in group),
                "strict_success_rate_all_scheduled": sum(r["strict_success"] for r in group)
                / len(group),
                "scenario_equal_requirement_score_all_scheduled": statistics.mean(
                    r["requirement_score_mean_all_scheduled"] for r in scenarios
                ),
                "validation_completed": len(validated),
                "validation_valid": sum(r["validation_valid"] is True for r in validated),
                "validation_valid_rate_completed": sum(r["validation_valid"] is True for r in validated)
                / len(validated)
                if validated
                else None,
                "mean_workflow_seconds_completed": statistics.mean(times) if times else None,
                "policy_coverage_complete_cases": sum(r["policy_coverage_complete"] for r in group),
            }
        )
    summary = {
        "phase": manifest["phase"],
        "scheduled_cases": len(schedule),
        "retained_cases": len(available),
        "complete_collection": len(available) == len(schedule)
        and all(r["terminal_status"] != "interrupted" for r in cases),
        "conditions": summaries,
        "measurement_note": "All scheduled cases remain in strict-success denominators. Unassessed requirement scores contribute zero to the conservative all-scheduled score and remain null in case rows; completed-only means are separate. Static outcomes do not establish deployment or runtime behaviour. Error counts are findings and can overlap.",
        "guardrailed_minus_baseline_success_rate": summaries[1]["strict_success_rate_all_scheduled"]
        - summaries[0]["strict_success_rate_all_scheduled"],
    }
    write_json(output / "summary.json", summary)
    write_text(output / "cases.csv", csv_text(cases))
    write_text(output / "by_scenario.csv", csv_text(scenario_rows))
    write_text(output / "requirements.csv", csv_text(requirements))
    write_text(output / "policies.csv", csv_text(policies))
    write_text(
        output / "errors.csv",
        csv_text(
            [
                {"condition": c, "category": k, "outcome": o, "findings": n}
                for (c, k, o), n in sorted(errors.items())
            ]
        ),
    )
    write_text(output / "report.md", report_markdown(summary))
    _chart(summaries, output / "success_rates.svg")
    _audit(root, schedule, available, output / "audit")
    return summary


def report_markdown(summary: dict) -> str:
    lines = [
        "# Experimental results",
        "",
        f"Phase: {summary['phase']}; retained {summary['retained_cases']}/{summary['scheduled_cases']}.",
        "",
        "| Condition | Strict successes / scheduled | Validation valid / completed | Policy coverage complete |",
        "|---|---:|---:|---:|",
    ]
    for row in summary["conditions"]:
        lines.append(
            f"| {row['condition']} | {row['successes']}/{row['scheduled']} | {row['validation_valid']}/{row['validation_completed']} | {row['policy_coverage_complete_cases']}/{row['scheduled']} |"
        )
    lines.extend(
        [
            "",
            summary["measurement_note"],
            "",
            "No independent-scenario inference is made from the three repetitions.",
            "",
        ]
    )
    return "\n".join(lines)


def _chart(rows: list[dict], output: Path) -> None:
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="680" height="230" viewBox="0 0 680 230">',
        '<rect width="680" height="230" fill="white"/>',
        '<g font-family="sans-serif" fill="#152534"><text x="25" y="32" font-size="19">Strict case success / all scheduled cases</text>',
    ]
    for i, row in enumerate(rows):
        y = 65 + 65 * i
        width = 400 * row["strict_success_rate_all_scheduled"]
        parts.extend(
            [
                f'<text x="25" y="{y + 22}">{row["condition"]}</text>',
                f'<rect x="150" y="{y}" width="400" height="32" fill="#e8edf2"/>',
                f'<rect x="150" y="{y}" width="{width:.2f}" height="32" fill="#247b91"/>',
                f'<text x="570" y="{y + 22}">{row["successes"]}/{row["scheduled"]}</text>',
            ]
        )
    parts.extend(
        [
            '<text x="25" y="218" font-size="12">Static benchmark; deployment and runtime connectivity were not assessed.</text></g></svg>'
        ]
    )
    write_text(output, "\n".join(parts))


def _audit(root: Path, schedule: list[dict], available: dict, output: Path) -> None:
    selected = [
        r for r in schedule if r["scenario_id"] in {"B01", "B03", "B06", "B08"} and r["repetition"] == 1
    ]
    for success in [True, False]:
        if any(available.get(r["case_id"], {}).get("strict_success") is success for r in selected):
            continue
        already = {r["case_id"] for r in selected}
        candidate = next(
            (
                r
                for r in schedule
                if r["case_id"] in available
                and r["case_id"] not in already
                and available[r["case_id"]].get("strict_success") is success
            ),
            None,
        )
        if candidate:
            selected.append(candidate)
    # Keep selection key separate from condition-hidden HCL copies.
    mapping, review = [], []
    for i, row in enumerate(selected, 1):
        aid = f"A{i:02}"
        case = root / "cases" / row["case_id"]
        if not (case / "final.json").exists():
            continue
        pointer = load(case / "final.json")
        source = case / pointer["terraform"] if pointer.get("terraform") else None
        if source and source.is_dir():
            import shutil

            shutil.copytree(source, output / "blinded" / aid)
        mapping.append({"audit_id": aid, **row})
        review.append(
            {
                "audit_id": aid,
                "reviewer": "",
                "reviewed_at": "",
                "requirement_mappings_checked": "",
                "issues": "",
                "adjudication": "",
            }
        )
    write_text(output / "selection_key.csv", csv_text(mapping))
    write_text(output / "review.csv", csv_text(review))
