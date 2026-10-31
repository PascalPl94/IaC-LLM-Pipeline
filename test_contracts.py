import copy

import pytest
from pydantic import ValidationError

from iac_experiments.models import GeneratedOutput, Scenario
from iac_experiments.protocol import asset_root, rejection_suite, schedule
from iac_experiments.runner import retain_output
from iac_experiments.storage import canonical, load, loads, write_json


def test_benchmark_and_dev_are_valid_and_distinct():
    for group, count in [("benchmark", 8), ("dev", 2)]:
        paths = list((asset_root() / group).glob("*.json"))
        assert len(paths) == count
        for path in paths:
            scenario = Scenario.model_validate(load(path))
            scenario.assert_supported()
            assert len(scenario.spokes) in [1, 2]


def test_input_strictness(scenario):
    raw = scenario.model_dump()
    raw["spokes"][0]["subnet"]["min_usable_addresses"] = "60"
    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)
    raw = scenario.model_dump()
    raw["routing"] = {}
    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


def test_rejection_suite_is_separate(tmp_path):
    report = rejection_suite(tmp_path / "rejections.json")
    assert report["all_pass"] and report["denominator"] == 6
    assert all(row["llm_calls"] == 0 for row in report["tests"])


@pytest.mark.parametrize(
    "filename",
    [
        "../main.tf",
        "/tmp/main.tf",
        "C:\\main.tf",
        "main.tf/other",
        "variables.tf",
        "main.tf.json",
        "MAIN.TF",
    ],
)
def test_writer_rejects_paths_and_does_not_write(tmp_path, generated, filename):
    generated["terraform_files"][0]["path"] = filename
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    bundle, errors = retain_output(
        {"status": "received", "text": canonical(generated), "finish_reason": "stop"}, attempt
    )
    assert bundle is None and errors
    assert not (attempt / "terraform").exists()


def test_raw_terraform_is_preserved(tmp_path, generated):
    bundle, errors = retain_output(
        {"status": "received", "text": canonical(generated), "finish_reason": "stop"}, tmp_path
    )
    assert not errors
    for file in generated["terraform_files"]:
        assert (bundle / file["path"]).read_bytes() == file["content"].encode()
    with pytest.raises(FileExistsError):
        write_json(tmp_path / "parsed_response.json", {})


def test_duplicate_json_key_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        loads('{"status":"ok","status":"unsupported"}')
    with pytest.raises(ValueError):
        loads('{"x":NaN}')


def test_duplicate_filename_rejected(generated):
    generated["terraform_files"].append(copy.deepcopy(generated["terraform_files"][0]))
    with pytest.raises(ValidationError, match="Duplicate"):
        GeneratedOutput.model_validate(generated)


def test_truncated_response_is_never_success(tmp_path, generated):
    bundle, errors = retain_output(
        {"status": "received", "text": canonical(generated), "finish_reason": "length"}, tmp_path
    )
    assert bundle and errors[0]["rule_id"] == "OUTPUT_COMPLETION"


def test_schedule_balanced_blocked_and_deterministic():
    ids = [f"B{i:02}" for i in range(1, 9)]
    rows = schedule(ids, 42)
    assert len(rows) == 48 == len({r["case_id"] for r in rows})
    assert rows == schedule(ids, 42) and rows != schedule(ids, 43)
    assert sum(r["condition"] == "baseline" for r in rows) == 24
    for i in range(3):
        assert {r["repetition"] for r in rows[i * 16 : (i + 1) * 16]} == {i + 1}
