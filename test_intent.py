import re

import pytest

from iac_experiments.evaluator import evaluate
from iac_experiments.fixtures import reference_output
from iac_experiments.hcl import parse_bundle
from iac_experiments.intent import evaluate_intent, requirements
from iac_experiments.models import Scenario
from iac_experiments.protocol import asset_root
from iac_experiments.storage import load


@pytest.mark.parametrize("sid", [f"B{i:02}" for i in range(1, 9)])
def test_all_reference_topologies_pass(sid, tmp_path):
    scenario = Scenario.model_validate(load(asset_root() / "benchmark" / f"{sid}.json"))
    for file in reference_output(scenario)["terraform_files"]:
        (tmp_path / file["path"]).write_text(file["content"])
    graph = parse_bundle(tmp_path)
    assert not graph.issues
    assert evaluate_intent(scenario, graph)["all_pass"]


@pytest.mark.parametrize(
    "mutation,category",
    [
        (lambda t: t.replace('"10.1.0.0/16"', '"10.0.0.0/16"'), "addressing"),
        (
            lambda t: re.sub(
                r'resource "azurerm_virtual_network_peering" "spoke1_to_hub" \{.*?\n\}\n',
                "",
                t,
                flags=re.S,
            ),
            "connectivity",
        ),
        (
            lambda t: re.sub(
                r'resource "azurerm_subnet_network_security_group_association" "spoke1" \{.*?\n\}\n',
                "",
                t,
                flags=re.S,
            ),
            "security",
        ),
        (
            lambda t: t.replace('source_address_prefix = "10.0.0.0/16"', 'source_address_prefix = "*"'),
            "security",
        ),
        (lambda t: t.replace("priority = 200", "priority = 90"), "security"),
    ],
)
def test_mutations_fail_without_changing_denominator(bundle, scenario, mutation, category):
    path = bundle / "main.tf"
    path.write_text(mutation(path.read_text()))
    result = evaluate_intent(scenario, parse_bundle(bundle))
    assert not result["all_pass"]
    assert result["applicable"] == len(requirements(scenario))
    assert any(r["category"] == category and r["outcome"] == "fail" for r in result["requirements"])


def test_resource_labels_are_not_semantic_names(bundle, scenario):
    path = bundle / "main.tf"
    text = (
        path.read_text().replace('"hub"', '"totally_different"').replace(".hub.", ".totally_different.")
    )
    path.write_text(text)
    assert evaluate_intent(scenario, parse_bundle(bundle))["all_pass"]


def test_unknown_expression_is_unresolved(bundle, scenario):
    path = bundle / "main.tf"
    path.write_text(path.read_text().replace('["10.1.0.0/16"]', '[cidrsubnet("10.1.0.0/8", 8, 0)]'))
    graph = parse_bundle(bundle)
    result = evaluate_intent(scenario, graph)
    assert result["unresolved"] > 0 and not result["all_pass"]
    assert any(i["outcome"] == "unresolved" for i in graph.issues)


@pytest.mark.parametrize(
    "addition",
    [
        'module "bad" { source="./anything" }',
        'data "azurerm_client_config" "bad" {}',
        'locals { name="x" }',
        'resource "null_resource" "bad" {}',
        "# checkov:skip=CKV_AZURE_10:ignore",
    ],
)
def test_excluded_hcl_never_reaches_external_tools(
    bundle, scenario, config, tmp_path, addition, monkeypatch
):
    path = bundle / "main.tf"
    path.write_text(path.read_text() + "\n" + addition + "\n")

    def forbidden(*args, **kwargs):
        pytest.fail("Unsafe HCL reached an external tool")

    monkeypatch.setattr("iac_experiments.evaluator.assess_tools", forbidden)
    result = evaluate(scenario, bundle, tmp_path / "report", config, tmp_path)
    assert not result["strict_success"] and not result["output_contract"]["pass"]
    assert result["validation"]["status"] == "not_run"


def test_empty_resources_never_pass(bundle, scenario, config, tmp_path):
    (bundle / "main.tf").write_text("# empty\n")
    result = evaluate(scenario, bundle, tmp_path / "report", config, tmp_path)
    assert not result["strict_success"]
    assert result["intent"]["applicable"] == len(requirements(scenario))


@pytest.mark.parametrize(
    "text",
    [
        'resource = "not-a-block"',
        'provider = "not-a-block"',
        'terraform = "not-a-block"',
        'output = "not-a-block"',
    ],
)
def test_malformed_hcl_block_shapes_are_findings_not_crashes(tmp_path, text):
    (tmp_path / "main.tf").write_text(text + "\n")
    graph = parse_bundle(tmp_path)
    assert graph.issues and not graph.parsed


@pytest.mark.parametrize("constraint", ["1.9.8", "=1.9.8", "= 1.9.8"])
def test_equivalent_exact_pins_are_accepted(bundle, constraint):
    path = bundle / "versions.tf"
    path.write_text(path.read_text().replace("= 1.9.8", constraint).replace("= 4.50.0", "4.50.0"))
    assert not parse_bundle(bundle).issues


def test_containment_and_overlap_use_actual_hcl_parent(bundle, scenario):
    path = bundle / "main.tf"
    path.write_text(path.read_text().replace('"10.1.0.0/16"', '"10.0.0.0/16"'))
    scored = {
        r["requirement_id"]: r for r in evaluate_intent(scenario, parse_bundle(bundle))["requirements"]
    }
    assert scored["ADDRESSING_ACTUAL_VNET_NONOVERLAP"]["outcome"] == "fail"
    assert scored["spoke1.subnet.capacity_containment"]["outcome"] == "fail"
