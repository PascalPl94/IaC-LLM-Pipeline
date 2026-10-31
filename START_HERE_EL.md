# Start here: Gemini / Python 3.12

Version 1.2.0 · 25 October 2026. Use a **new folder** named `MSc_IaC_Experiments_Gemini_v1_2`. Keep the earlier OpenAI installation separate. Copy only the commands inside the code blocks.

## 1. Installation

Open Terminal in the new folder and run:

```bash
bash setup.sh 3.12
```

Continue when `Setup complete using Python 3.12` appears. The configuration is prepared for `gemini-3.8-flash`; no manual JSON editing is needed. Setup makes no API calls.

## 2. Gemini API key and connection check

Visit https://aistudio.google.com/apikey with your Google account and create a Gemini API key in the appropriate project. For use without monetary charges, select a project listed as **Free Tier**. A 5 TB student subscription alone provides no information about the API quota. See `GEMINI_ACCOUNT_EL.md` for Cloud credits and the limits relevant to one day of collection.

```bash
.venv312/bin/iac-exp check-api --config config.json --out runs/gemini-api-01 --prompt-key
```

When `Gemini API key (input hidden):` appears, paste only the complete key and press Enter. The characters remain hidden. There is no need to use `export` or `read`, or to put the key in a file.

Continue only with `api_ready: true`. If the check fails, preserve the full message and stop at that point. Use a new folder for another attempt, such as `runs/gemini-api-02`. The check makes a small, actual generation request with up to one explicit retry; its records remain separate from the experiments.

## 3. Local verification without LLM calls

```bash
.venv312/bin/iac-exp verify --config config.json --out runs/gemini-verify-01
```

Continue only with `all_pass: true`. If verification fails, preserve the diagnostics and address the cause. Repeat verification in a new folder, then point the freeze commands to the successful verification. Switching to Gemini requires new verification; the earlier OpenAI `verify-02` is not used.

## 4. Pilot: four cases

```bash
.venv312/bin/iac-exp freeze --config config.json --verification runs/gemini-verify-01 --phase pilot --out runs/gemini-pilot-01
```

Run one case first:

```bash
.venv312/bin/iac-exp run runs/gemini-pilot-01 --limit 1 --prompt-key
```

The command will request the Gemini key again through hidden input. If `technical_failure` appears, stop and read the detailed message. A `quality_failure` is a valid experimental outcome, rather than a technical blocker.

Then run the remaining cases:

```bash
.venv312/bin/iac-exp run runs/gemini-pilot-01 --prompt-key
```

After execution finishes:

```bash
.venv312/bin/iac-exp readiness runs/gemini-pilot-01
```

The first completed case is skipped. Continue only with `ready: true`, after also checking the available API quota. No timing estimate is calculated from pilots with technical failures. If the configuration or code needs to change, complete new verify and pilot stages with new folder names.

## 5. The 48 main cases

```bash
.venv312/bin/iac-exp freeze --config config.json --verification runs/gemini-verify-01 --phase main --pilot runs/gemini-pilot-01 --out runs/gemini-main-01
.venv312/bin/iac-exp run runs/gemini-main-01 --prompt-key
```

Keep the model, Python version, configuration, evaluator, prompts and scenarios frozen until the collection ends. The application stops at the first technical failure and preserves all evidence. Do not delete or selectively repeat completed failures. For an interruption or Ctrl+C, follow the recovery procedure in the full README.

## 6. Analysis and completion

```bash
.venv312/bin/iac-exp analyse runs/gemini-main-01 --out runs/gemini-main-01-analysis
```

Open `summary.json`, `report.md` and the CSV files. Perform the review recorded in `audit/review.csv` and retain a second copy of the main and analysis folders. Diagnostics, API checks and pilots do not count towards the 48 main cases.

Workflow times include pacing. The fields `pacing_wait_seconds` and `llm_transport_seconds` are recorded separately. Do not report zero monetary cost without confirming the project's Free Tier status or the credits actually applied.

Python 3.15 remains supported, using `bash setup.sh 3.15` and `.venv315/bin/iac-exp` throughout. For this continuation, use the existing Python 3.12 installation.
