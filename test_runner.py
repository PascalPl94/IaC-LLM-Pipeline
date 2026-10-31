from iac_experiments.analysis import analyse
from iac_experiments.protocol import asset_root, schedule
from iac_experiments.provider import estimate_cost, request_payload
from iac_experiments.runner import initial_messages, run_case
from iac_experiments.storage import canonical, load, write_json


def setup_root(tmp_path, scenario):
    root = tmp_path / "experiment"
    write_json(root / "frozen/inputs/B01.json", scenario.model_dump())
    for name in ["system.txt", "repair.txt"]:
        target = root / "frozen/prompts" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((asset_root() / "prompts" / name).read_text())
    return root


def row(condition):
    return {
        "case_id": f"B01_{condition}_R1",
        "scenario_id": "B01",
        "condition": condition,
        "repetition": 1,
        "order": 1,
    }


def stubs(generated):
    calls, checks = [], []

    def generate(messages, config, repetition, output):
        output.mkdir()
        calls.append(messages)
        return {
            "status": "received",
            "text": canonical(generated),
            "finish_reason": "stop",
            "calls": [{"duration_seconds": 1}],
            "usage": {"total_tokens": 100},
            "cost_usd_estimate": None,
            "cost_unavailable_reason": "test",
        }

    def evaluate(scenario, bundle, output, config, root, errors, lock):
        checks.append(str(output))
        output.mkdir(parents=True)
        result = {
            "strict_success": False,
            "technical_blocker": False,
            "intent": {"score": 0.5},
            "feedback": [{"rule_id": "TEST_FINDING"}],
            "duration_seconds": 0.1,
        }
        write_json(output / "assessment.json", result)
        return result

    return generate, evaluate, calls, checks


def test_baseline_never_repairs_and_initial_prompts_are_identical(tmp_path, scenario, generated, config):
    root = setup_root(tmp_path, scenario)
    generate, evaluate, calls, checks = stubs(generated)
    result = run_case(row("baseline"), root, config, generate, evaluate)
    assert result["repair_count"] == 0 and len(calls) == 1
    assert len(checks) == 1 and "/evaluation/final" in checks[0]
    baseline_prompt = calls[0]
    assert baseline_prompt == initial_messages(scenario, root, config)
    generate2, evaluate2, calls2, _ = stubs(generated)
    run_case(row("guardrailed"), root, config, generate2, evaluate2)
    assert calls2[0] == baseline_prompt


def test_guardrailed_budget_last_attempt_and_retention(tmp_path, scenario, generated, config):
    root = setup_root(tmp_path, scenario)
    generate, evaluate, calls, _ = stubs(generated)
    result = run_case(row("guardrailed"), root, config, generate, evaluate)
    case = root / "cases" / "B01_guardrailed_R1"
    assert len(calls) == 3 and result["repair_count"] == 2
    assert load(case / "final.json")["attempt"] == 2
    assert all((case / f"attempts/{i}/terraform/main.tf").exists() for i in range(3))
    assert "TEST_FINDING" in calls[1][-1]["content"]
    assert len(calls[2]) == 6  # The second repair retains the complete conversation.
    assert (case / "evaluation/initial/assessment.json").exists()


def test_gemini_repairs_retain_all_native_assistant_parts(tmp_path, scenario, generated, config):
    root = setup_root(tmp_path, scenario)
    original, evaluate, calls, _ = stubs(generated)

    def generate(messages, config, repetition, output):
        response = original(messages, config, repetition, output)
        response["assistant_parts"] = [
            {"text": response["text"], "thoughtSignature": f"sig-{len(calls)}"}
        ]
        return response

    changed = config.model_copy(update={"provider": "gemini", "model": "gemini-3.8-flash"})
    run_case(row("guardrailed"), root, changed, generate, evaluate)
    assert len(calls) == 3
    assert calls[1][2]["gemini_parts"][0]["thoughtSignature"] == "sig-1"
    assert calls[2][2]["gemini_parts"][0]["thoughtSignature"] == "sig-1"
    assert calls[2][4]["gemini_parts"][0]["thoughtSignature"] == "sig-2"


def test_request_configuration_common_for_both_conditions(config):
    messages = [{"role": "system", "content": "JSON"}]
    payload = request_payload(messages, config, 1)
    assert "seed" not in payload and "temperature" not in payload
    assert payload["n"] == 1 and payload["store"] is False


def test_unknown_cost_is_null(config):
    assert estimate_cost({"prompt_tokens": 5, "completion_tokens": 2}, config, False)[0] is None


def test_analysis_keeps_all_48_scheduled_cases(tmp_path):
    root = tmp_path / "experiment"
    write_json(root / "experiment_manifest.json", {"phase": "main"})
    write_json(root / "frozen/schedule.json", schedule([f"B{i:02}" for i in range(1, 9)], 3))
    summary = analyse(root, tmp_path / "analysis")
    assert not summary["complete_collection"] and summary["retained_cases"] == 0
    assert all(
        c["scheduled"] == 24 and c["strict_success_rate_all_scheduled"] == 0
        for c in summary["conditions"]
    )
    assert all(c["validation_valid_rate_completed"] is None for c in summary["conditions"])
    assert (tmp_path / "analysis/cases.csv").read_text().count("\n") == 49


def test_analysis_retains_missing_gemini_token_counters_as_unknown(tmp_path):
    root = tmp_path / "experiment"
    rows = schedule([f"B{i:02}" for i in range(1, 9)], 3)
    write_json(root / "experiment_manifest.json", {"phase": "main"})
    write_json(root / "frozen/schedule.json", rows)
    write_json(
        root / "cases" / rows[0]["case_id"] / "result.json",
        {
            "terminal_status": "quality_failure",
            "data_origin": "real_llm",
            "attempts": [{"usage": {"total_tokens": None}, "transport_calls": 1}],
        },
    )
    summary = analyse(root, tmp_path / "analysis")
    assert summary["retained_cases"] == 1 and not summary["complete_collection"]
    import csv

    with (tmp_path / "analysis/cases.csv").open() as handle:
        first = next(csv.DictReader(handle))
    assert first["observed_tokens"] == "" and first["token_usage_complete"] == "False"
