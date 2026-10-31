import copy

import pytest

from iac_experiments.fixtures import reference_output
from iac_experiments.models import ExperimentConfig, Scenario
from iac_experiments.protocol import asset_root
from iac_experiments.storage import load


@pytest.fixture
def scenario():
    return Scenario.model_validate(load(asset_root() / "benchmark/B01.json"))


@pytest.fixture
def generated(scenario):
    return copy.deepcopy(reference_output(scenario))


@pytest.fixture
def config():
    return ExperimentConfig(model="test-model", technical_retries=0)


@pytest.fixture
def bundle(tmp_path, generated):
    root = tmp_path / "terraform"
    root.mkdir()
    for f in generated["terraform_files"]:
        (root / f["path"]).write_text(f["content"], encoding="utf-8")
    return root
