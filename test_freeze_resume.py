import sys

import pytest

from iac_experiments.models import ExperimentConfig
from iac_experiments.protocol import assert_frozen, batch, freeze, pilot_readiness, source_files
from iac_experiments.storage import canonical, hash_file, load, write_json, write_text


def verified(tmp_path):
    verification = tmp_path / "verification"
    verification.mkdir()
    (verification / "provider-cache").mkdir()
    write_text(verification / "provider.lock.hcl", "# unit-test-only\n")
    config = ExperimentConfig(
        model="test-model", terraform_bin=sys.executable, checkov_bin=sys.executable
    )
    env = {
        "python": sys.version,
        "terraform_executable": sys.executable,
        "checkov_executable": sys.executable,
        "packages": {},
        "executable_sha256": {
            "terraform": hash_file(__import__("pathlib").Path(sys.executable)),
            "checkov": hash_file(__import__("pathlib").Path(sys.executable)),
        },
    }
    write_json(
        verification / "verification.json",
        {
            "all_pass": True,
            "source_files": source_files(),
            "config": config.model_dump(),
            "lockfile_sha256": hash_file(verification / "provider.lock.hcl"),
            "environment": env,
        },
    )
    write_json(verification / "rejection.json", {"all_pass": True})
    return config, verification


def test_modified_frozen_input_blocks_resume(tmp_path):
    config, verification = verified(tmp_path)
    root = tmp_path / "pilot"
    freeze(config, verification, root, pilot=True)
    assert assert_frozen(root)["scheduled_cases"] == 4
    (root / "frozen/inputs/DEV01.json").write_text("{}")
    with pytest.raises(ValueError, match="Frozen file changed"):
        assert_frozen(root)


def test_resume_skips_completed_and_preserves_interrupted_without_new_call(tmp_path, monkeypatch):
    config, verification = verified(tmp_path)
    root = tmp_path / "pilot"
    freeze(config, verification, root, pilot=True)
    schedule = load(root / "frozen/schedule.json")
    first = root / "cases" / schedule[0]["case_id"]
    write_json(first / "result.json", {"strict_success": False})
    second = root / "cases" / schedule[1]["case_id"]
    second.mkdir(parents=True)
    write_text(second / "retained-evidence.txt", "keep me")

    def forbidden(*args, **kwargs):
        pytest.fail("Resume repeated a completed or interrupted case")

    monkeypatch.setattr("iac_experiments.protocol.run_case", forbidden)
    with pytest.raises(ValueError, match="Interrupted case"):
        batch(root, limit=1)
    results = batch(root, limit=1, recover_interrupted=True)
    assert results[0]["terminal_status"] == "interrupted"
    assert (second / "retained-evidence.txt").read_text() == "keep me"


def test_main_freeze_requires_real_pilot(tmp_path):
    config, verification = verified(tmp_path)
    with pytest.raises(ValueError, match="Main freeze requires"):
        freeze(config, verification, tmp_path / "main", pilot=False)


def test_python_change_after_verification_blocks_freeze(tmp_path):
    config, verification = verified(tmp_path)
    path = verification / "verification.json"
    report = load(path)
    report["environment"]["python"] = "another Python runtime"
    path.write_text(canonical(report))
    with pytest.raises(ValueError, match="Python runtime changed since verification"):
        freeze(config, verification, tmp_path / "pilot", pilot=True)


def test_python_change_after_freeze_blocks_resume(tmp_path):
    config, verification = verified(tmp_path)
    root = tmp_path / "pilot"
    freeze(config, verification, root, pilot=True)
    path = root / "experiment_manifest.json"
    report = load(path)
    report["environment"]["python"] = "another Python runtime"
    path.write_text(canonical(report))
    (root / "experiment_manifest.sha256").write_text(hash_file(path) + "\n")
    with pytest.raises(ValueError, match="Python runtime changed"):
        batch(root)


def test_provider_change_requires_new_verification(tmp_path):
    config, verification = verified(tmp_path)
    changed = config.model_copy(update={"provider": "gemini", "model": "gemini-3.8-flash"})
    with pytest.raises(ValueError, match="Config differs"):
        freeze(changed, verification, tmp_path / "pilot", pilot=True)


def test_batch_stops_after_first_technical_failure(tmp_path, monkeypatch):
    config, verification = verified(tmp_path)
    root = tmp_path / "pilot"
    freeze(config, verification, root, pilot=True)
    calls = []

    def fail(row, root, config):
        calls.append(row)
        result = {
            **row,
            "terminal_status": "technical_failure",
            "data_origin": "real_llm",
            "strict_success": False,
        }
        write_json(root / "cases" / row["case_id"] / "result.json", result)
        return result

    monkeypatch.setattr("iac_experiments.protocol.run_case", fail)
    assert len(batch(root)) == 1 and len(calls) == 1


def test_failed_pilot_durations_do_not_produce_runtime_estimate(tmp_path):
    config, verification = verified(tmp_path)
    root = tmp_path / "pilot"
    freeze(config, verification, root, pilot=True)
    for row in load(root / "frozen/schedule.json"):
        write_json(
            root / "cases" / row["case_id"] / "result.json",
            {
                **row,
                "data_origin": "real_llm",
                "terminal_status": "technical_failure",
                "strict_success": False,
                "workflow_duration_seconds": 0.2,
                "common_evaluation_duration_seconds": 0.001,
            },
        )
    report = pilot_readiness(root)
    assert not report["ready"] and report["completed_pilot_cases"] == 0
    assert report["technical_pilot_cases"] == 4
    assert report["estimated_main_seconds_with_25pct_margin"] is None
