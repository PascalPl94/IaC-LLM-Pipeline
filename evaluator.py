"""Common assessment, invoked afresh from retained code for either condition."""

from __future__ import annotations

import time
from pathlib import Path

from .hcl import Graph, parse_bundle
from .intent import evaluate_intent
from .models import ExperimentConfig, Scenario
from .storage import write_json
from .tooling import assess_tools, policy_report


def evaluate(
    scenario: Scenario,
    bundle: Path | None,
    output: Path,
    config: ExperimentConfig,
    root: Path,
    contract_errors: list[dict] | None = None,
    lockfile: Path | None = None,
) -> dict:
    start = time.monotonic()
    output.mkdir(parents=True, exist_ok=False)
    graph = parse_bundle(bundle) if bundle else Graph(parsed=False)
    contract = [*(contract_errors or []), *graph.issues]
    intent = evaluate_intent(scenario, graph)
    tools = None
    policy = {
        "status": "not_run",
        "coverage_complete": False,
        "all_pass": False,
        "reason": "Unsafe, malformed or out-of-contract Terraform was not passed to external tools.",
    }
    # No init/provider execution before the entire restricted output contract passes.
    if bundle and graph.parsed and not contract:
        tools = assess_tools(bundle, output / "external", config, root, lockfile)
        policy = policy_report(tools["scanner"], scenario, graph)
    validation = tools["validation"] if tools else {"status": "not_run", "valid": None}
    strict = bool(
        not contract
        and intent["all_pass"]
        and validation["status"] == "completed"
        and validation["valid"] is True
        and policy["all_pass"]
    )
    technical = bool(tools and (validation["status"] == "blocked" or policy["status"] == "blocked"))
    feedback = [
        {
            "rule_id": row["requirement_id"],
            "category": row["category"],
            "outcome": row["outcome"],
            "expected": row["expected"],
            "actual": row["actual"],
            "evidence": row["evidence"],
        }
        for row in intent["requirements"]
        if row["outcome"] != "pass"
    ]
    feedback.extend(contract)
    if tools and validation["status"] == "completed" and validation["valid"] is False:
        feedback.append(
            {
                "rule_id": "TERRAFORM_VALIDATE",
                "category": "provider_schema",
                "diagnostics": validation.get("diagnostics", []),
            }
        )
    if not policy["all_pass"]:
        feedback.append({"rule_id": "CHECKOV_POLICY", "category": "security", "policy": policy})
    result = {
        "strict_success": strict,
        "technical_blocker": technical,
        "intent": intent,
        "output_contract": {"pass": not contract, "findings": contract},
        "validation": validation,
        "formatting": tools["formatting"] if tools else None,
        "policy": policy,
        "feedback": feedback,
        "plan": "not_run",
        "deployment": "not_run",
        "duration_seconds": time.monotonic() - start,
    }
    write_json(output / "assessment.json", result)
    return result
