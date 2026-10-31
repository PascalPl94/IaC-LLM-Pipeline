# Release 1.2.0 verification: Gemini

25 October 2026. CPython 3.12 and 3.15 are supported. This release was checked on Linux x86_64 with **Python 3.12.14 and 3.15.0**, using the distributed 1.2.0 wheel.

## Checks that passed

- **88 unit/regression tests per Python version**, with no failures or errors. Coverage retains schema, HCL, network intent, policy coverage, baseline/repair separation, immutable evidence, freeze and resume checks.
- The new tests use MockTransport for native Gemini requests and responses, system/user/model roles, full repair history, thought-signature replay, JSON output, thinking-token accounting and raw-envelope retention.
- Checks covered JSON/list/string/HTML errors, complete key redaction, detailed diagnostics in place of empty messages, transient retries with the same payload, server retry delays, daily-quota exhaustion without retry, and the waiting-time limit.
- Checks covered the interactive API-key prompt without shell exports, the supplied Gemini configuration, the separate check-api command, stopping the batch at the first technical failure, and withholding unsupported timing estimates when pilots fail.
- Missing native usage counters and the preservation of unknown values during analysis were checked.
- Dependency checks passed in both environments. Pinned runtime versions remain unchanged; HTTPX 0.28.1 is now also declared as a direct dependency.
- Ruff checks and the setup.sh syntax check passed. The wheel was compared byte for byte against all distributed package Python files and assets.
- Checkov 3.3.30 was run on handwritten HCL fixtures: valid patterns A/B achieved complete selected coverage and passing results; CKV_AZURE_10/9 detected public SSH/RDP. These are software checks, rather than LLM observations.

## Checks required on the user's computer

- **No actual Gemini/OpenAI model call was made while preparing this release.** The preparation environment had no access to the user's API key, project quota, student entitlement or Cloud credits. The local check-api command must receive a successful response from an actual API call.
- Full Terraform verification was not repeated for this build. AzureRM provider startup had been blocked in the previous delivery environment. A new local verify must report all_pass: true before freezing, even if the earlier OpenAI verification passed on the user's Mac.
- Release 1.2.0 was not run natively on macOS or Windows during preparation. Setup supports the same 3.12/3.15 options and now defaults to 3.12. Python 3.15 on a Mac may require Command Line Tools to build dependencies from source.
- The API check does not establish that the user's quota covers all 48 main cases in one day. See GEMINI_ACCOUNT_EL.md.
- No Azure deployments or Terraform plan/apply operations were performed. Preparing this release did not produce main experimental results.

The files validation_evidence/tests-python312.xml, tests-python315.xml and tool_checks.json document these checks. They are not a local verification manifest and do not waive the requirement for new verify/pilot/freeze stages.
