# MSc experiment code: Gemini

Version 1.2.0 · 25 October 2026 · Paschalis Plastiras

This package implements the reduced core of the “LLM-Based Infrastructure-as-Code Network Architect with Guardrails”. The default is **Gemini 3.8 Flash**, accessed through Google's native generateContent API. The baseline and guardrailed conditions use the same model and initial prompt. The benchmark remains 8 × 2 × 3 = 48 main cases, with two separate development inputs and six rejection tests.

Install this release in a **new installation folder**. Keep the earlier OpenAI folder and its logs separate. New verification, a new Gemini pilot and a new freeze are required. Code, fixtures and simulated test responses do not constitute experimental results.

Read **START_HERE_EL.md** first. **GEMINI_ACCOUNT_EL.md** explains API keys, the Free Tier, student Google AI Pro benefits and Cloud credits. **VERIFICATION_REPORT.md** records what was checked and what still requires confirmation on your Mac.

## 1. Installation

In Terminal, from the newly extracted folder:

```bash
bash setup.sh 3.12
```

Python 3.12, used in the existing environment, is now the default. Python 3.15 is supported through `bash setup.sh 3.15`, but keep exactly the same Python version from verification through analysis. Setup locates Python.org/uv paths, installs the locked dependencies, the 1.2.0 wheel and Terraform 1.9.8, checks dependencies and runs the tests. It makes no API calls.

Activate the environment to use the `iac-exp` commands in this full guide:

```bash
source .venv312/bin/activate
```

Alternatively, use `.venv312/bin/iac-exp` throughout. This form does not require activation. The short guide uses explicit paths so that PATH changes and shell exports are unnecessary.

Use a clean environment. Do not add `python-hcl2`: it conflicts with Checkov's `bc-python-hcl2`. Do not copy the earlier OpenAI configuration or frozen runs into the new installation. Setup creates a Gemini configuration with absolute tool paths and the selected model identifier.

## 2. API key and a separate connection check

Obtain a **Gemini API key** from https://aistudio.google.com/apikey. Signing into the Gemini app or holding a 5 TB subscription does not replace an API key.

```bash
iac-exp check-api --config config.json --out runs/gemini-api-01 --prompt-key
```

When `Gemini API key (input hidden):` appears, paste only the complete key and press Enter. No characters are displayed. The application retains the key only within that Python process. There is no need to use `read` or `export`, or to save the key in a file.

Continue with `api_ready: true`. The check performs a small, actual JSON generation, retains its request, envelope and usage separately, and does not count towards the pilot or main collection. This single response does not verify active API quotas, eligibility for student benefits or the project's billing status.

If an API error occurs, the full redacted message, HTTP status, response body and any request identifier are saved in `response/generation.json`. Use a new output folder for another attempt, such as `runs/gemini-api-02`.

The `GEMINI_API_KEY` environment variable is supported but optional. The `--prompt-key` flag requires fresh hidden input even when an older key exists in the environment. The native Gemini adapter sends the key in an HTTP header to `generativelanguage.googleapis.com`, rather than in a query string. Gemini runs do not call OpenAI. The legacy OpenAI adapter remains available for compatibility only when `provider: openai` is explicitly selected.

The supplied configuration selects `gemini-3.8-flash`, `low` thinking effort, one candidate, JSON MIME output, a 12,000-token output limit including thinking, and a minimum interval of 15 seconds between API request starts. There is no automatic model or provider switching. A 15-second interval does not guarantee compliance with the account's RPM, TPM or RPD limits.

Monetary values remain `null` until verified prices per million tokens, a pricing source and a date are supplied. A student subscription does not establish that usage is free. Actual native token counters are recorded, with thinking tokens retained separately. If retry attempts have unknown usage, the total cost remains unknown.

## 3. Verification without LLM calls

```bash
iac-exp verify --config config.json --out runs/gemini-verify-01
```

Wait for exit code 0 and `"all_pass": true`. Read `runs/gemini-verify-01/verification.json`. The checks include safe patterns A/B, overlapping CIDRs, missing reverse peering, a missing association, invalid NSG priority, an unsupported expression, an empty configuration and public SSH/RDP. The installed Checkov checks **CKV_AZURE_9 and CKV_AZURE_10** are tested on NSGs and standalone rules, with expected coverage derived from the input. The six input rejection tests run separately, without API calls.

If the AzureRM provider cannot start, a tool is missing, a timeout occurs or scanner coverage is absent, **verification does not permit freezing the collection**. This is a required readiness condition. Inspect stdout/stderr and the relevant `assessment.json`, address the environment problem and repeat verification in a **new** folder, such as `runs/gemini-verify-02`. Never edit `verification.json` manually to make verification pass.

Terraform runs only `fmt -check`, `init -backend=false` and `validate -json`. Checkov uses an explicit check allowlist and an isolated configuration. There is no `plan`, `apply`, Azure login, cloud-resource creation or upload of scan results to a service. Provider installation is local and does not establish that deployment will succeed.

## 4. Four actual pilot cases on the development inputs

```bash
iac-exp freeze --config config.json --verification runs/gemini-verify-01 --phase pilot --out runs/gemini-pilot-01
iac-exp run runs/gemini-pilot-01 --prompt-key
```

After execution finishes:

```bash
iac-exp readiness runs/gemini-pilot-01
```

DEV01 and DEV02 run once per condition: **4 cases**, with up to 8 logical LLM calls before any technical retries. They do not count towards the 48 main cases. Readiness requires all four cases to be completed and at least one full guardrailed success. It provides a rough time estimate for the main experiment with a 25% margin. Two pilot cases per condition cannot guarantee a completion time.

If the prompt, model settings or code change after a pilot failure, complete new verification and pilot stages in new folders. Development and tuning are permitted on the DEV inputs before the main freeze.

## 5. Freeze and run the 48 main cases

```bash
iac-exp freeze --config config.json --verification runs/gemini-verify-01 --phase main --pilot runs/gemini-pilot-01 --out runs/gemini-main-01
iac-exp run runs/gemini-main-01
```

This creates **8 × 2 × 3 = 48** unique cases, 24 per condition. Before execution, the schedule is randomised within three repetition blocks of 16. There is one worker and no LLM-response caching. Each baseline case has one generation without corrective feedback. Each guardrailed case has one generation and up to two repairs. The initial prompt and model settings are identical across conditions for the same input and repetition.

The maximum number of logical LLM calls is **96 for the main collection**. Up to one technical retry with the same payload is allowed for each affected call; physical HTTP requests and uncertain charges are recorded separately. API 400/401/403 responses and quality failures are not repeated as technical retries. The runner continues after quality failures and stops at the first technical failure, displaying API diagnostics where available. An unexpected internal exception stops execution for inspection, protecting the collection from repeated software errors.

To stop after a controlled number of cases, use a command such as `iac-exp run runs/gemini-main-01 --limit 16`. Run the same command without a limit to continue the pending cases. The application skips cases with a complete `result.json`.

The model does not have to succeed on every case for the experiment to be complete. Failures remain results. Do not edit HCL by hand or selectively rerun failed cases to improve the score.

## 6. Interruption and recovery

If Ctrl+C is pressed during a call, the provider may already have processed or charged for it. The runner does not automatically repeat the call. Inspect the incomplete case folder and run:

```bash
iac-exp run runs/gemini-main-01 --recover-interrupted
```

The incomplete case is retained as `interrupted` within the scheduled denominator, without another call. Completed cases are preserved, and execution proceeds to the remaining cases. Continuing with modified code requires a new experiment; hash checks prevent silent changes to the frozen evaluator.

If a forced termination leaves a `.runner.lock` file, first confirm that no process is running that experiment. Only then remove that specific stale lock. Active locks are not bypassed automatically.

## 7. Analysis and audit

```bash
iac-exp analyse runs/gemini-main-01 --out runs/gemini-main-01-analysis
```

Outputs include `cases.csv`, `by_scenario.csv`, `requirements.csv`, `policies.csv`, `errors.csv`, `summary.json`, `report.md` and a simple SVG chart. Partial reports are supported, with collection coverage stated explicitly. Use a new output folder for a second analysis.

Strict success requires an accepted output contract, complete intent success, successful Terraform validation and complete passing coverage of the selected policy checks. Formatting is reported separately and is not part of the primary success criterion. Missing or unresolved requirements remain in the original denominator. The strict-success denominator remains 24 per condition. Unmeasured values are blank or null. The conservative all-scheduled requirement aggregate assigns zero **contribution** to unevaluated cases and states this explicitly; separate completed-only means are also provided. Scenarios receive equal weight regardless of their number of requirements.

The audit selects R1 for B01/B03/B06/B08 in both conditions, adding up to two further cases only if needed to represent success and failure. The `audit/blinded/` folder contains HCL copies with the condition hidden; `selection_key.csv` provides the mapping, and `review.csv` contains fields for **your actual review**. Do not present this file as a completed or independent expert review before the review has taken place.

Retain a second copy of the complete `runs/gemini-main-01` folder and its analysis folder. Open a randomly selected raw response, Terraform bundle and CSV from the copy and compare their hashes. The delivery ZIP contains the code; it does not contain results from future runs.

## Records retained for each case

- Original and normalised input, and the initial prompt hash.
- Exact messages, request body, raw model text, provider envelope, response identifier, token usage and transport-attempt metadata.
- Immutable decoded Terraform files and SHA-256 hashes for each attempt.
- Guardrail reports, stdout/stderr, exit codes and timings.
- A `final.json` pointer to **the last attempt**, without best-of selection.
- Common final evaluation and the initial guardrailed assessment for before/after comparison.
- Workflow time measured separately from common-evaluation time, API transport duration and explicit pacing wait; all logs use UTC timestamps.

Secrets are not written into prompts or configuration. Terraform and Checkov subprocesses receive a restricted environment without Gemini, OpenAI, Azure or AWS credentials, or uncontrolled TF CLI arguments.

## Limitations and alignment with the draft

Read `METHODOLOGY_ALIGNMENT.md` before writing the Implementation and Evaluation chapters. The 7 October draft includes optional features outside the reduced final implementation. Conclusions apply only to this synthetic benchmark and the model actually used. Three repetitions do not create 24 independent scenarios. The common evaluator and guardrails share predicates and a parser; fixtures and a manual audit do not eliminate this validity threat.

Official documentation used for the implementation:

- https://ai.google.dev/api/generate-content
- https://ai.google.dev/gemini-api/docs/rate-limits
- https://ai.google.dev/gemini-api/docs/billing
- https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures

- https://developers.openai.com/api/reference/resources/chat
- https://github.com/openai/openai-python
- https://developer.hashicorp.com/terraform/cli/commands/validate
- https://developer.hashicorp.com/terraform/cli/commands/init
- https://www.checkov.io/2.Basics/CLI%20Command%20Reference.html
- https://github.com/bridgecrewio/checkov
- https://registry.terraform.io/providers/hashicorp/azurerm/4.50.0/docs

## Further details of the Gemini release

Repairs retain the full history of the current case and replay native Gemini content parts and thought signatures unchanged. The evaluator reads only the final visible JSON/HCL, rather than signature metadata. The baseline still performs a single generation without feedback.

Readiness does not estimate main-collection time from pilots that are incomplete or have technical failures. An estimate appears only when all four cases have completed as success/quality_failure. Readiness also requires at least one strict guardrailed success.

The cases.csv export includes pacing_wait_seconds and llm_transport_seconds. The workflow_seconds field includes pacing waits. Do not interpret imposed waiting time as the model's inference speed. Include these waits when scheduling the collection.

Preserve the outcome after a technical failure. For development pilots, address the cause before running new verification and a new pilot under a new name. In the main collection, do not selectively rerun completed failures: they remain within the scheduled denominator. Continuing the same frozen collection executes only pending cases.
