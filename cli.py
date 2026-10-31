"""Terminal entry points; commands perform no Azure deployment."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .analysis import analyse
from .api_probe import check_api
from .api_support import api_key
from .models import ExperimentConfig, GeneratedOutput, Scenario
from .protocol import asset_root, batch, freeze, pilot_readiness, rejection_suite, verify
from .storage import canonical, load, write_json


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="iac-exp", description="Bounded Azure Terraform research experiments"
    )
    sub = cli.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create local configuration, schemas and reference inputs")
    init.add_argument("--directory", type=Path, default=Path.cwd())
    for name in ["verify", "freeze"]:
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("config.json"))
        command.add_argument("--out", type=Path, required=True)
        if name == "freeze":
            command.add_argument("--verification", type=Path, required=True)
            command.add_argument("--phase", choices=["pilot", "main"], required=True)
            command.add_argument("--pilot", type=Path)
    run = sub.add_parser("run", help="Make real billable model calls for remaining scheduled cases")
    run.add_argument("root", type=Path)
    run.add_argument("--limit", type=int)
    run.add_argument("--recover-interrupted", action="store_true")
    run.add_argument(
        "--prompt-key", action="store_true", help="Prompt securely even if an environment key exists"
    )
    probe = sub.add_parser("check-api", help="One real API smoke check, outside benchmark data")
    probe.add_argument("--config", type=Path, default=Path("config.json"))
    probe.add_argument("--out", type=Path, required=True)
    probe.add_argument("--prompt-key", action="store_true")
    readiness = sub.add_parser("readiness")
    readiness.add_argument("root", type=Path)
    analysis = sub.add_parser("analyse")
    analysis.add_argument("root", type=Path)
    analysis.add_argument("--out", type=Path, required=True)
    reject = sub.add_parser("reject-tests")
    reject.add_argument("--out", type=Path, required=True)
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            args.directory.mkdir(parents=True, exist_ok=True)
            targets = [
                args.directory / "config.json",
                args.directory / "reference-inputs",
                args.directory / "schemas",
            ]
            if any(p.exists() for p in targets):
                raise ValueError(
                    "init targets already exist; use a new directory. Existing files were preserved."
                )
            shutil.copyfile(asset_root() / "config.example.json", targets[0])
            shutil.copytree(asset_root() / "benchmark", targets[1] / "benchmark")
            shutil.copytree(asset_root() / "dev", targets[1] / "dev")
            write_json(targets[2] / "input.schema.json", Scenario.model_json_schema())
            write_json(targets[2] / "output.schema.json", GeneratedOutput.model_json_schema())
            print(
                f"Created {args.directory.resolve()}. Gemini defaults are ready; inputs are inspection copies."
            )
        elif args.command in {"verify", "freeze"}:
            config = ExperimentConfig.model_validate(load(args.config))
            if args.command == "verify":
                report = verify(config, args.out.resolve())
                print(
                    canonical(
                        {"all_pass": report["all_pass"], "report": str(args.out / "verification.json")}
                    )
                )
                return 0 if report["all_pass"] else 2
            report = freeze(
                config,
                args.verification.resolve(),
                args.out.resolve(),
                args.phase == "pilot",
                args.pilot.resolve() if args.pilot else None,
            )
            print(canonical({"phase": report["phase"], "scheduled_cases": report["scheduled_cases"]}))
        elif args.command == "run":
            if args.limit is not None and args.limit < 1:
                raise ValueError("--limit must be positive.")
            config = ExperimentConfig.model_validate(
                load(args.root / "experiment_manifest.json")["config"]
            )
            api_key(config, interactive=True, force_prompt=args.prompt_key)
            results = batch(args.root.resolve(), args.limit, args.recover_interrupted)
            return 2 if any(r.get("terminal_status") == "technical_failure" for r in results) else 0
        elif args.command == "check-api":
            config = ExperimentConfig.model_validate(load(args.config))
            api_key(config, interactive=True, force_prompt=args.prompt_key)
            report = check_api(config, args.out.resolve())
            print(canonical(report))
            return 0 if report["api_ready"] else 2
        elif args.command == "readiness":
            report = pilot_readiness(args.root.resolve())
            print(canonical(report))
            return 0 if report["ready"] else 2
        elif args.command == "analyse":
            print(canonical(analyse(args.root.resolve(), args.out.resolve())))
        elif args.command == "reject-tests":
            report = rejection_suite(args.out.resolve())
            print(canonical(report))
            return 0 if report["all_pass"] else 2
    except (ValueError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(
            "Interrupted. Evidence retained. See README recovery procedure before resuming.",
            file=sys.stderr,
        )
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
