"""Bounded, shell-free subprocesses with isolated home and minimal environment."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from importlib.metadata import version
from pathlib import Path

from .models import CHECK_IDS, ExperimentConfig, Scenario
from .storage import hash_file, utcnow, write_json, write_text


def tool_environment(home: Path, cache: Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    allow = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "https_proxy",
        "http_proxy",
        "no_proxy",
    }
    env = {k: v for k, v in os.environ.items() if k in allow}
    env.update(
        {
            "HOME": str(home.resolve()),
            "USERPROFILE": str(home.resolve()),
            "TF_IN_AUTOMATION": "1",
            "CHECKPOINT_DISABLE": "1",
            "TF_INPUT": "0",
            "TF_PLUGIN_CACHE_DIR": str(cache.resolve()),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "LOG_LEVEL": "ERROR",
            "BC_SKIP_MAPPING": "TRUE",
            "CHECKOV_SKIP_MAPPING": "TRUE",
        }
    )
    cli_config = home / "terraform.rc"
    if not cli_config.exists():
        write_text(cli_config, "disable_checkpoint = true\n")
    env["TF_CLI_CONFIG_FILE"] = str(cli_config.resolve())
    return env


def execute(
    argv: list[str], cwd: Path, env: dict[str, str], timeout: float, output: Path, retries: int = 1
) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    invocations = []
    for retry in range(retries + 1):
        started, stamp = time.monotonic(), utcnow()
        status, code, stdout, stderr = "completed", None, "", ""
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=os.name != "nt",
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                code = proc.returncode
                if code < 0:
                    status = "crash"
            except subprocess.TimeoutExpired:
                status = "timeout"
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                stdout, stderr = proc.communicate()
                code = proc.returncode
        except OSError as exc:
            status, stderr = "tool_error", f"{type(exc).__name__}: {exc}"
        except BaseException:
            if "proc" in locals() and proc.poll() is None:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.communicate()
            raise
        elapsed = time.monotonic() - started
        item = {
            "argv": argv,
            "started_at": stamp,
            "finished_at": utcnow(),
            "duration_seconds": elapsed,
            "status": status,
            "exit_code": code,
            "retry_index": retry,
        }
        write_text(output / f"{retry}.stdout.txt", stdout)
        write_text(output / f"{retry}.stderr.txt", stderr)
        write_json(output / f"{retry}.json", item)
        invocations.append(item)
        if status not in {"timeout", "crash"}:
            break
    result = {
        **invocations[-1],
        "invocations": invocations,
        "duration_seconds": sum(row["duration_seconds"] for row in invocations),
        "stdout": stdout,
        "stderr": stderr,
    }
    write_json(output / "result.json", result)
    return result


def environment_report(config: ExperimentConfig, root: Path) -> dict:
    env = tool_environment(root / "home", root / "provider-cache")
    tf = shutil.which(config.terraform_bin)
    ck = shutil.which(config.checkov_bin)
    if not tf or not ck:
        raise ValueError(
            "Terraform and Checkov must be installed and on PATH (or set absolute executable paths)."
        )
    report = {
        "python": __import__("sys").version,
        "platform": __import__("platform").platform(),
        "packages": {
            name: version(name)
            for name in [
                "msc-iac-experiments",
                "pydantic",
                "bc-python-hcl2",
                "openai",
                "httpx",
                "checkov",
            ]
        },
        "terraform_executable": str(Path(tf).absolute()),
        "checkov_executable": str(Path(ck).absolute()),
    }
    report["executable_sha256"] = {"terraform": hash_file(Path(tf)), "checkov": hash_file(Path(ck))}
    for name, argv in [("terraform", [tf, "version", "-json"]), ("checkov", [ck, "--version"])]:
        result = execute(argv, root, env, 60, root / f"version-{name}", retries=0)
        if result["status"] != "completed" or result["exit_code"] != 0:
            raise ValueError(f"Cannot identify {name} version; see {root}.")
        report[name] = (
            json.loads(result["stdout"])["terraform_version"]
            if name == "terraform"
            else result["stdout"].strip()
        )
    if report["terraform"] != config.terraform_version or report["checkov"] != config.checkov_version:
        raise ValueError(
            f"Version mismatch: Terraform {report['terraform']}, Checkov {report['checkov']}; expected {config.terraform_version}, {config.checkov_version}."
        )
    return report


def assess_tools(
    bundle: Path, report: Path, config: ExperimentConfig, root: Path, lockfile: Path | None = None
) -> dict:
    work = report / "work"
    work.mkdir(parents=True, exist_ok=False)
    for path in sorted(bundle.glob("*.tf")):
        shutil.copyfile(path, work / path.name)
    if lockfile:
        shutil.copyfile(lockfile, work / ".terraform.lock.hcl")
    env = tool_environment(report / "home", root / "provider-cache")
    cfg = report / "empty-checkov.yml"
    write_text(cfg, "{}\n")
    tf, ck = config.terraform_bin, config.checkov_bin
    fmt = execute(
        [tf, "fmt", "-check", "-diff", "-no-color", "."],
        work,
        env,
        config.tool_timeout_seconds,
        report / "fmt",
        config.technical_retries,
    )
    init_args = [tf, "init", "-backend=false", "-input=false", "-no-color"]
    if lockfile:
        init_args.append("-lockfile=readonly")
    init = execute(
        init_args, work, env, config.init_timeout_seconds, report / "init", config.technical_retries
    )
    valid = None
    validation = {
        "status": "blocked",
        "valid": None,
        "reason": "Terraform init did not complete successfully.",
    }
    if init["status"] == "completed" and init["exit_code"] == 0:
        valid = execute(
            [tf, "validate", "-json"],
            work,
            env,
            config.tool_timeout_seconds,
            report / "validate",
            config.technical_retries,
        )
        try:
            parsed = json.loads(valid["stdout"])
            value = parsed.get("valid")
            if type(value) is not bool:
                raise ValueError("Missing boolean valid field")
            validation = {
                "status": "completed",
                "valid": value,
                "diagnostics": parsed.get("diagnostics", []),
                "exit_code": valid["exit_code"],
            }
            infrastructure_markers = (
                "failed to load plugin schemas",
                "failed to obtain provider schema",
                "failed to instantiate provider",
                "does not match any of the checksums",
                "required plugins are not installed",
            )
            diagnostic_text = json.dumps(parsed.get("diagnostics", [])).lower()
            if any(marker in diagnostic_text for marker in infrastructure_markers):
                validation = {
                    "status": "blocked",
                    "valid": None,
                    "reason": "Provider installation or plugin startup failed; not a generated-code validity measurement.",
                    "diagnostics": parsed.get("diagnostics", []),
                }
            if valid["status"] != "completed" or valid["exit_code"] not in (0, 1):
                validation = {"status": "blocked", "valid": None, "reason": "Terraform process failed."}
        except (ValueError, TypeError):
            validation = {
                "status": "blocked",
                "valid": None,
                "reason": "Terraform did not return usable validation JSON.",
            }
    scan = execute(
        [
            ck,
            "--config-file",
            str(cfg.resolve()),
            "--directory",
            str(work.resolve()),
            "--framework",
            "terraform",
            "--check",
            ",".join(CHECK_IDS),
            "--output",
            "json",
            "--skip-download",
            "--skip-results-upload",
        ],
        work,
        env,
        config.tool_timeout_seconds,
        report / "checkov",
        config.technical_retries,
    )
    result = {
        "formatting": {
            "status": fmt["status"],
            "pass": fmt["status"] == "completed" and fmt["exit_code"] == 0,
        },
        "initialization": {"status": init["status"], "exit_code": init["exit_code"]},
        "validation": validation,
        "scanner": scan,
        "plan": "not_run",
        "deployment": "not_run",
        "lockfile_sha256": hash_file(work / ".terraform.lock.hcl")
        if (work / ".terraform.lock.hcl").exists()
        else None,
    }
    write_json(report / "tools.json", result)
    return result


def policy_report(scan: dict, scenario: Scenario, graph) -> dict:
    """Check both IDs on every declared NSG and standalone rule, plus input-based cardinality."""
    n = len(scenario.spokes)
    expected_count = n * (3 if scenario.security_pattern == "A" else 4) * len(CHECK_IDS)
    expected = {
        (check, resource.address)
        for check in CHECK_IDS
        for resource in graph.resources.values()
        if resource.kind in {"azurerm_network_security_group", "azurerm_network_security_rule"}
    }
    completed, parsing_errors, instances = False, [], []
    try:
        data = json.loads(scan.get("stdout", ""))
        documents = data if isinstance(data, list) else [data]
        for doc in documents:
            if doc.get("check_type") != "terraform":
                continue
            for result_name, outcome in [
                ("passed_checks", "pass"),
                ("failed_checks", "fail"),
                ("skipped_checks", "skipped"),
            ]:
                for row in doc.get("results", {}).get(result_name, []):
                    if row.get("check_id") in CHECK_IDS:
                        instances.append(
                            {
                                "check_id": row["check_id"],
                                "resource": row.get("resource"),
                                "outcome": outcome,
                                "file": row.get("file_path"),
                                "line_range": row.get("file_line_range"),
                            }
                        )
            parsing_errors.extend(doc.get("results", {}).get("parsing_errors", []))
        completed = scan.get("status") == "completed" and scan.get("exit_code") in (0, 1)
    except (ValueError, TypeError, AttributeError):
        pass
    observed = {(x["check_id"], x["resource"]) for x in instances if x["outcome"] in {"pass", "fail"}}
    passed = sum(x["outcome"] == "pass" for x in instances)
    failed = sum(x["outcome"] == "fail" for x in instances)
    full = (
        completed
        and not parsing_errors
        and len(expected) == expected_count
        and expected == observed
        and len(instances) == expected_count
    )
    return {
        "status": "completed" if completed else "blocked",
        "selected_ids": list(CHECK_IDS),
        "expected_instances": expected_count,
        "executed_instances": passed + failed,
        "passed": passed,
        "failed": failed,
        "skipped": sum(x["outcome"] == "skipped" for x in instances),
        "pass_rate": passed / (passed + failed) if passed + failed else None,
        "coverage_complete": full,
        "missing_instances": [list(x) for x in sorted(expected - observed)],
        "parsing_errors": parsing_errors,
        "instances": instances,
        "all_pass": full and failed == 0 and all(x["outcome"] == "pass" for x in instances),
    }
