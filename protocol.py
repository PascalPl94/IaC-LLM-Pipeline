"""Verification, freezing, deterministic schedules and immutable batch execution."""

from __future__ import annotations

import csv
import io
import random
import re
import shutil
import sys
from importlib.resources import files
from pathlib import Path

from .evaluator import evaluate
from .intent import requirements
from .models import ExperimentConfig, Scenario
from .runner import run_case
from .storage import (
    RunLock,
    canonical,
    event,
    hash_file,
    input_gate,
    load,
    utcnow,
    write_json,
    write_text,
)
from .tooling import environment_report, execute, tool_environment


def asset_root() -> Path:
    return Path(str(files("iac_experiments").joinpath("assets")))


def source_files() -> dict[str, str]:
    package = Path(str(files("iac_experiments")))
    return {
        str(p.relative_to(package)): hash_file(p)
        for p in sorted(package.rglob("*"))
        if p.is_file() and p.suffix in {".py", ".json", ".txt"} and "__pycache__" not in p.parts
    }


def rejection_suite(output: Path) -> dict:
    results = []
    for path in sorted((asset_root() / "rejection").glob("*.json")):
        test = load(path)
        _, errors = input_gate(test["input"])
        results.append(
            {
                "test_id": test["test_id"],
                "expected_rule": test["expected_rule"],
                "passed": any(e["rule_id"] == test["expected_rule"] for e in errors),
                "errors": errors,
                "llm_calls": 0,
            }
        )
    report = {
        "suite": "input_rejection",
        "denominator": len(results),
        "passed": sum(x["passed"] for x in results),
        "all_pass": all(x["passed"] for x in results),
        "tests": results,
    }
    write_json(output, report)
    return report


def verify(config: ExperimentConfig, root: Path) -> dict:
    from .fixtures import block, reference_output
    from .runner import retain_output

    root.mkdir(parents=True, exist_ok=False)
    env = environment_report(config, root)
    config = config.model_copy(
        update={"terraform_bin": env["terraform_executable"], "checkov_bin": env["checkov_executable"]}
    )
    write_json(root / "environment.json", env)
    listing = execute(
        [
            config.checkov_bin,
            "--list",
            "--framework",
            "terraform",
            "--check",
            "CKV_AZURE_9,CKV_AZURE_10",
        ],
        root,
        tool_environment(root / "home", root / "provider-cache"),
        config.tool_timeout_seconds,
        root / "checkov-catalogue",
        0,
    )
    if (
        listing["status"] != "completed"
        or listing["exit_code"] != 0
        or any(c not in listing["stdout"] for c in ["CKV_AZURE_9", "CKV_AZURE_10"])
    ):
        raise ValueError("Selected Checkov checks not available; see checkov-catalogue.")
    s = Scenario.model_validate(load(asset_root() / "benchmark/B01.json"))
    valid = reference_output(s)
    cases = [
        ("valid_A", s, valid, True, None),
        (
            "valid_B",
            Scenario.model_validate(load(asset_root() / "benchmark/B07.json")),
            reference_output(Scenario.model_validate(load(asset_root() / "benchmark/B07.json"))),
            True,
            None,
        ),
    ]
    import copy

    for name, mutate, expected_check in [
        ("overlap", lambda t: t.replace('"10.1.0.0/16"', '"10.0.0.0/16"'), None),
        (
            "missing_reverse_peering",
            lambda t: re.sub(
                r'resource "azurerm_virtual_network_peering" "spoke1_to_hub" \{.*?\n\}\n',
                "",
                t,
                flags=re.S,
            ),
            None,
        ),
        (
            "missing_association",
            lambda t: re.sub(
                r'resource "azurerm_subnet_network_security_group_association" "spoke1" \{.*?\n\}\n',
                "",
                t,
                flags=re.S,
            ),
            None,
        ),
        ("invalid_provider_value", lambda t: t.replace("priority = 100", "priority = 99"), "validate"),
        (
            "unknown_expression",
            lambda t: t.replace('["10.1.0.0/16"]', '[cidrsubnet("10.1.0.0/8", 8, 0)]'),
            "unresolved",
        ),
        ("empty_resources", lambda t: "", None),
    ]:
        output = copy.deepcopy(valid)
        output["terraform_files"][-1]["content"] = (
            mutate(output["terraform_files"][-1]["content"]) or "# empty fixture\n"
        )
        cases.append((name, s, output, False, expected_check))
    for port, check in [(22, "CKV_AZURE_10"), (3389, "CKV_AZURE_9")]:
        output = copy.deepcopy(valid)
        output["terraform_files"][-1]["content"] += block(
            "azurerm_network_security_rule",
            f"public_{port}",
            {
                "name": f"public-{port}",
                "resource_group_name": s.resource_group,
                "network_security_group_name": "exp-spoke1-nsg",
                "priority": 150,
                "direction": "Inbound",
                "access": "Allow",
                "protocol": "Tcp",
                "source_port_range": "*",
                "destination_port_range": str(port),
                "source_address_prefix": "*",
                "destination_address_prefix": "*",
            },
        )
        cases.append((f"public_{port}", s, output, False, check))
    results, lock = [], None
    for name, scenario, output, expected_success, special in cases:
        attempt = root / "fixtures" / name
        attempt.mkdir(parents=True)
        generation = {
            "status": "received",
            "finish_reason": "stop",
            "refusal": None,
            "text": canonical(output),
        }
        bundle, errors = retain_output(generation, attempt)
        report = evaluate(scenario, bundle, attempt / "evaluation", config, root, errors, lock)
        candidate_lock = attempt / "evaluation/external/work/.terraform.lock.hcl"
        if lock is None and candidate_lock.exists():
            shutil.copyfile(candidate_lock, root / "provider.lock.hcl")
            lock = root / "provider.lock.hcl"
        passed = report["strict_success"] == expected_success and not report["technical_blocker"]
        if special == "validate":
            passed = passed and report["validation"].get("valid") is False
        elif special == "unresolved":
            passed = passed and report["intent"]["unresolved"] > 0
        elif special:
            passed = passed and any(
                x["check_id"] == special and x["outcome"] == "fail"
                for x in report["policy"].get("instances", [])
            )
        results.append(
            {
                "fixture": name,
                "passed": passed,
                "expected_success": expected_success,
                "observed_success": report["strict_success"],
                "technical_blocker": report["technical_blocker"],
                "intent_all_pass": report["intent"]["all_pass"],
                "validation_status": report["validation"]["status"],
                "validation_valid": report["validation"].get("valid"),
                "policy_all_pass": report["policy"]["all_pass"],
                "policy_failed_ids": sorted(
                    {
                        x["check_id"]
                        for x in report["policy"].get("instances", [])
                        if x["outcome"] == "fail"
                    }
                ),
            }
        )
        print(f"verify {name}: {'PASS' if passed else 'FAIL'}", flush=True)
    rejection = rejection_suite(root / "rejection.json")
    report = {
        "data_origin": "software_verification_only",
        "created_at": utcnow(),
        "all_pass": bool(lock and all(x["passed"] for x in results) and rejection["all_pass"]),
        "config": config.model_dump(),
        "source_files": source_files(),
        "environment": env,
        "fixtures": results,
        "rejection_all_pass": rejection["all_pass"],
        "lockfile_sha256": hash_file(lock) if lock else None,
    }
    write_json(root / "verification.json", report)
    return report


def schedule(scenario_ids: list[str], seed: int, pilot: bool = False) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for repetition in range(1, 2 if pilot else 4):
        block = [
            {
                "case_id": f"{sid}_{condition}_R{repetition}",
                "scenario_id": sid,
                "condition": condition,
                "repetition": repetition,
            }
            for sid in scenario_ids
            for condition in ["baseline", "guardrailed"]
        ]
        rng.shuffle(block)
        rows.extend(block)
    return [{"order": i, **row} for i, row in enumerate(rows, 1)]


def freeze(
    config: ExperimentConfig, verification: Path, root: Path, pilot: bool, pilot_root: Path | None = None
) -> dict:
    check = load(verification / "verification.json")
    if check["environment"].get("python") != sys.version:
        raise ValueError("Python runtime changed since verification; rerun verify with this Python.")
    if not check["all_pass"] or check["source_files"] != source_files():
        raise ValueError("Verification failed or source changed; rerun verify before freezing.")
    if hash_file(verification / "provider.lock.hcl") != check["lockfile_sha256"]:
        raise ValueError("Verification lockfile changed.")
    prior = ExperimentConfig.model_validate(check["config"])
    # Executable aliases may resolve to the same actual paths; all experimental settings must match.
    resolved = config.model_copy(
        update={"terraform_bin": prior.terraform_bin, "checkov_bin": prior.checkov_bin}
    )
    if resolved != prior:
        raise ValueError("Config differs from verification. Use the same config or rerun verify.")
    config = resolved
    pilot_report = None
    if not pilot:
        if pilot_root is None:
            raise ValueError(
                "Main freeze requires --pilot pointing to four completed real development cases."
            )
        assert_frozen(pilot_root)
        pm = load(pilot_root / "experiment_manifest.json")
        if pm["phase"] != "pilot" or pm["config"] != config.model_dump():
            raise ValueError("Pilot phase/config mismatch.")
        pilot_report = pilot_readiness(pilot_root)
        if not pilot_report["ready"]:
            raise ValueError("Pilot not ready: " + canonical(pilot_report))
    root.mkdir(parents=True, exist_ok=False)
    frozen = root / "frozen"
    frozen.mkdir()
    shutil.copytree(asset_root() / "prompts", frozen / "prompts")
    inputs = asset_root() / ("dev" if pilot else "benchmark")
    shutil.copytree(inputs, frozen / "inputs")
    shutil.copyfile(verification / "provider.lock.hcl", frozen / "provider.lock.hcl")
    shutil.copyfile(verification / "verification.json", frozen / "verification.json")
    # Provider cache is installation-only; generated LLM outputs are never cached.
    shutil.copytree(verification / "provider-cache", root / "provider-cache", symlinks=False)
    package = Path(str(files("iac_experiments")))
    for relative in source_files():
        destination = frozen / "source" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package / relative, destination)
    ids = []
    for path in sorted((frozen / "inputs").glob("*.json")):
        scenario = Scenario.model_validate(load(path))
        scenario.assert_supported()
        ids.append(scenario.scenario_id)
        write_json(
            frozen / "rubrics" / path.name,
            [
                {"requirement_id": r.id, "category": r.category, "expected": r.expected}
                for r in requirements(scenario)
            ],
        )
    rows = schedule(ids, config.schedule_seed, pilot)
    write_json(frozen / "schedule.json", rows)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    write_text(frozen / "run_schedule.csv", buffer.getvalue())
    environment = check["environment"]
    manifest = {
        "protocol_version": 1,
        "benchmark_version": 2,
        "created_at": utcnow(),
        "phase": "pilot" if pilot else "main",
        "data_origin": "real_llm_only",
        "config": config.model_dump(),
        "environment": environment,
        "source_files": source_files(),
        "workers": 1,
        "scheduled_cases": len(rows),
        "pilot_readiness": pilot_report,
        "frozen_files": {
            str(p.relative_to(root)): hash_file(p) for p in sorted(frozen.rglob("*")) if p.is_file()
        },
        "assumptions": [
            "Existing resource group; no Azure authentication or deployment performed.",
            "Common evaluator shares predicates with generation guardrails.",
            "Input-schema efficacy assessed separately by six rejection tests.",
            "LLM transport retries may incur unknown billable usage.",
        ],
    }
    write_json(root / "experiment_manifest.json", manifest)
    write_text(root / "experiment_manifest.sha256", hash_file(root / "experiment_manifest.json") + "\n")
    write_json(root / "rejection_results.json", load(verification / "rejection.json"))
    return manifest


def assert_frozen(root: Path) -> dict:
    manifest = load(root / "experiment_manifest.json")
    if manifest["environment"].get("python") != sys.version:
        raise ValueError(
            "Python runtime changed; resume with the exact Python used for this experiment."
        )
    if (
        hash_file(root / "experiment_manifest.json")
        != (root / "experiment_manifest.sha256").read_text().strip()
    ):
        raise ValueError("Manifest changed; preserve old run and freeze a new experiment.")
    if manifest["source_files"] != source_files():
        raise ValueError(
            "Installed source differs from frozen source; do not resume with modified code."
        )
    for relative, expected in manifest["frozen_files"].items():
        if not (root / relative).is_file() or hash_file(root / relative) != expected:
            raise ValueError(f"Frozen file changed or missing: {relative}")
    from importlib.metadata import version

    for name, expected in manifest["environment"]["packages"].items():
        if version(name) != expected:
            raise ValueError(f"Dependency version changed: {name}")
    for name in ["terraform", "checkov"]:
        executable = Path(manifest["environment"][f"{name}_executable"])
        if (
            not executable.is_file()
            or hash_file(executable) != manifest["environment"]["executable_sha256"][name]
        ):
            raise ValueError(f"Executable changed: {name}")
    return manifest


def batch(root: Path, limit: int | None = None, recover_interrupted: bool = False) -> list[dict]:
    manifest = assert_frozen(root)
    config = ExperimentConfig.model_validate(manifest["config"])
    rows = load(root / "frozen/schedule.json")
    done = []
    with RunLock(root):
        for row in rows:
            case = root / "cases" / row["case_id"]
            if (case / "result.json").exists():
                continue
            if case.exists():
                if not recover_interrupted:
                    raise ValueError(
                        f"Interrupted case {row['case_id']}; inspect it, then use --recover-interrupted to retain it as interrupted without repeating API calls."
                    )
                result = {
                    **row,
                    "data_origin": "real_llm",
                    "terminal_status": "interrupted",
                    "strict_success": False,
                    "intent_score": None,
                    "reason": "Interrupted case retained without another model call; inspect incomplete evidence.",
                }
                write_json(case / "result.json", result)
                event(root, "interrupted_case_retained", case_id=row["case_id"])
                done.append(result)
            else:
                result = run_case(row, root, config)
                done.append(result)
                print(
                    f"[{row['order']}/{len(rows)}] {row['case_id']}: {result['terminal_status']}",
                    flush=True,
                )
            if result.get("terminal_status") == "technical_failure":
                print(
                    f"Stopped after a technical failure. See {case / 'evaluation/final/assessment.json'}",
                    flush=True,
                )
                generation = (
                    case / "attempts" / str(result.get("final_attempt", 0)) / "response/generation.json"
                )
                if generation.is_file():
                    for call in load(generation).get("calls", []):
                        error = call.get("error")
                        if error:
                            print(
                                f"API HTTP {error.get('status_code')}: {error.get('provider_message', '')[:1500]}",
                                flush=True,
                            )
                break
            if limit is not None and len(done) >= limit:
                break
    return done


def pilot_readiness(root: Path) -> dict:
    rows = load(root / "frozen/schedule.json")
    results = [
        load(root / "cases" / r["case_id"] / "result.json")
        for r in rows
        if (root / "cases" / r["case_id"] / "result.json").exists()
    ]
    completed = [
        r
        for r in results
        if r["data_origin"] == "real_llm" and r["terminal_status"] in {"success", "quality_failure"}
    ]
    real_complete = len(completed) == 4
    means = {}
    for condition in ["baseline", "guardrailed"]:
        values = [
            r["workflow_duration_seconds"]
            for r in completed
            if r["condition"] == condition and "workflow_duration_seconds" in r
        ]
        means[condition] = sum(values) / len(values) if values else None
    evaluation = [
        r["common_evaluation_duration_seconds"]
        / (2 if r["condition"] == "guardrailed" and r.get("repair_count", 0) > 0 else 1)
        for r in completed
        if "common_evaluation_duration_seconds" in r
    ]
    evaluator_mean = sum(evaluation) / len(evaluation) if evaluation else None
    estimated = None
    if real_complete and all(v is not None for v in means.values()) and evaluator_mean is not None:
        estimated = (24 * means["baseline"] + 24 * means["guardrailed"] + 72 * evaluator_mean) * 1.25
    # Requires a real end-to-end guardrailed success and executed scanner/validation evidence.
    guard_success = any(r["condition"] == "guardrailed" and r["strict_success"] for r in results)
    return {
        "ready": real_complete and guard_success,
        "completed_pilot_cases": len(completed),
        "retained_pilot_cases": len(results),
        "technical_pilot_cases": len(results) - len(completed),
        "provider": load(root / "experiment_manifest.json")["config"].get("provider", "openai"),
        "api_budget_note": "Main: at most 96 model calls / 192 transport attempts. Pilot: 8 / 16. Add check-api calls. API quotas are account-specific.",
        "guardrailed_success_observed": guard_success,
        "mean_workflow_seconds": means,
        "mean_evaluator_seconds": evaluator_mean,
        "estimated_main_seconds_with_25pct_margin": estimated,
        "estimate_note": "Four development cases provide a rough planning estimate, not a runtime guarantee; allow separate audit and reporting time.",
    }
