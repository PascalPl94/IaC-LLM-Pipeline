import json
import sys

import pytest

from iac_experiments.hcl import parse_bundle
from iac_experiments.models import CHECK_IDS
from iac_experiments.tooling import assess_tools, execute, policy_report, tool_environment


def test_credentials_and_implicit_tool_config_are_removed(tmp_path, monkeypatch):
    for key in [
        "OPENAI_API_KEY",
        "ARM_CLIENT_SECRET",
        "AWS_SECRET_ACCESS_KEY",
        "BC_API_KEY",
        "TF_CLI_ARGS",
        "PYTHONPATH",
    ]:
        monkeypatch.setenv(key, "do-not-inherit")
    env = tool_environment(tmp_path / "home", tmp_path / "cache")
    for key in [
        "OPENAI_API_KEY",
        "ARM_CLIENT_SECRET",
        "AWS_SECRET_ACCESS_KEY",
        "BC_API_KEY",
        "TF_CLI_ARGS",
        "PYTHONPATH",
    ]:
        assert key not in env


def test_missing_executable_is_tool_failure(tmp_path):
    result = execute([str(tmp_path / "does-not-exist")], tmp_path, {}, 1, tmp_path / "out")
    assert result["status"] == "tool_error" and len(result["invocations"]) == 1


def test_timeout_has_at_most_one_identical_retry(tmp_path):
    args = [sys.executable, "-c", "import time; time.sleep(5)"]
    result = execute(args, tmp_path, {}, 0.05, tmp_path / "out", 1)
    assert result["status"] == "timeout" and len(result["invocations"]) == 2
    assert result["invocations"][0]["argv"] == result["invocations"][1]["argv"]


def test_configuration_failure_not_retried(tmp_path):
    result = execute([sys.executable, "-c", "raise SystemExit(1)"], tmp_path, {}, 1, tmp_path / "out", 1)
    assert result["status"] == "completed" and result["exit_code"] == 1
    assert len(result["invocations"]) == 1


def scanner(graph, skipped=False):
    rows = [
        {"check_id": c, "resource": r.address}
        for c in CHECK_IDS
        for r in graph.resources.values()
        if r.kind in {"azurerm_network_security_group", "azurerm_network_security_rule"}
    ]
    doc = {
        "check_type": "terraform",
        "results": {"skipped_checks" if skipped else "passed_checks": rows},
    }
    return {"status": "completed", "exit_code": 0, "stdout": json.dumps(doc)}


def test_policy_requires_actual_per_resource_coverage(bundle, scenario):
    graph = parse_bundle(bundle)
    assert policy_report(scanner(graph), scenario, graph)["all_pass"]
    empty = {"status": "completed", "exit_code": 0, "stdout": '{"check_type":"terraform","results":{}}'}
    report = policy_report(empty, scenario, graph)
    assert not report["all_pass"] and report["pass_rate"] is None
    assert not policy_report(scanner(graph, skipped=True), scenario, graph)["coverage_complete"]


def test_missing_resource_does_not_reduce_policy_denominator(bundle, scenario):
    graph = parse_bundle(bundle)
    graph.resources.pop("azurerm_network_security_rule.spoke1_p200")
    report = policy_report(scanner(graph), scenario, graph)
    assert report["expected_instances"] == 6
    assert not report["all_pass"]


@pytest.mark.parametrize(
    "summary,expected_status,expected_valid",
    [
        ("Failed to load plugin schemas", "blocked", None),
        ("Unsupported argument", "completed", False),
    ],
)
def test_provider_startup_failure_is_not_a_code_validity_failure(
    bundle, config, tmp_path, monkeypatch, summary, expected_status, expected_valid
):
    def fake_execute(argv, *args, **kwargs):
        if "validate" in argv:
            return {
                "status": "completed",
                "exit_code": 1,
                "stdout": json.dumps({"valid": False, "diagnostics": [{"summary": summary}]}),
                "stderr": "",
            }
        return {"status": "completed", "exit_code": 0, "stdout": "{}", "stderr": ""}

    monkeypatch.setattr("iac_experiments.tooling.execute", fake_execute)
    result = assess_tools(bundle, tmp_path / "report", config, tmp_path)
    assert result["validation"]["status"] == expected_status
    assert result["validation"]["valid"] is expected_valid
