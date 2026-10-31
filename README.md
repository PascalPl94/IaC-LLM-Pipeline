# MSc IaC experiments — Python 3.12 / 3.15

Version 1.1.0. Start with [START_HERE_EL.md](START_HERE_EL.md). Choose `bash setup.sh 3.12` or `bash setup.sh 3.15`, then activate the matching `.venv312` or `.venv315` environment. Setup stops at the first failure and preserves existing configuration. See [README_EL.md](README_EL.md) for the full protocol and [VERIFICATION_REPORT.md](VERIFICATION_REPORT.md) for actual checks and limitations.

The benchmark, prompts, repair allowance, evaluator and selected Checkov rules retain the same experimental design. The Python runtime is checked during freeze and resume. Software fixtures and unit tests are not LLM experiment data. No Terraform plan/apply is implemented.
