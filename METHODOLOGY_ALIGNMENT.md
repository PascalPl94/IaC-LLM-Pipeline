# Alignment between the implementation and the draft

This document is based on `MSc_Thesis_Draft_v02_20261007.docx`, supplied on 22 October 2026, and the reduced scope agreed afterwards. The draft itself was not modified during this work. The final system description needs to reflect the implementation choices below.

| Topic | Implementation to describe in the final report |
|---|---|
| Scope | One hub, 1–2 spokes, exactly one subnet per VNet and six permitted resource types. |
| Optional extensions | UDRs, Private DNS, gateways, firewalls and workloads are not implemented. Related theory may provide context, with a clear distinction between background and implemented features. |
| Benchmark | Exactly B01–B08, benchmark_version=2, two conditions and three repetitions: 48 cases. DEV01/DEV02 and IR01–IR06 remain separate. |
| Input validation | Strict Pydantic validation with extra=forbid, followed by domain checks. Main inputs are verified before freezing. Input rejection is measured separately from generation on valid inputs. |
| HCL | Literals and direct resource `.name`/`.id` references. Variables, locals, data sources, functions, dynamic blocks and modules are unsupported. Unsupported expressions produce a contract finding and an unresolved outcome wherever they affect a requirement. |
| Files | Required: versions.tf, provider.tf and main.tf. Optional: outputs.tf. The contract does not request variables.tf because variables are outside its final scope. |
| Network intent | Exact requested names, CIDRs, region and resource group; capacity and containment; peerings in both directions with fixed flags; separate spoke NSGs and associations; and exact NSG patterns A/B. Terraform resource labels may differ. |
| NSG analyser | Compares the complete required fixed rule set. It is not a general traffic simulator or a general analyser of priority and traffic-flow semantics. |
| Policy scanner | Checkov 3.3.30, with CKV_AZURE_9/10 verified on NSGs and standalone rules. OPA is not used. Coverage requires both input-based cardinality and observed check/resource instances. |
| Versions | Terraform 1.9.8, AzureRM 4.50.0 and HTTPX 0.28.1 for native Gemini REST. OpenAI SDK 2.54.0 is retained only for the optional legacy adapter. The model is explicitly selected in the configuration before the pilots and then frozen. These versions are reproducibility choices, rather than claims about the latest available releases. |
| Parser | bc-python-hcl2 0.4.3, as required by Checkov. The competing python-hcl2 package is not installed in the same environment. Sharing a parser is a validity limitation. |
| Strict success | Output contract, all intent requirements, completed and valid Terraform assessment, and complete selected-policy success and coverage. Formatting is recorded separately. Static success does not establish deployment or live connectivity. |
| Repairs | Up to two repairs, each returning a complete replacement bundle. All attempts are retained, and the final attempt is selected. Tool or transport outages do not provide additional opportunities to improve model output. |
| Baseline | The same initial prompt and settings, one generation and no corrective feedback. Common assessment runs only after the workflow ends. |
| Reproducibility | A manifest, hashes for code and inputs, dependencies, executable hashes, provider lock file, prompts, model settings and a randomised schedule. One worker. |
| Time and cost | Workflow and common evaluation are measured separately; actual usage is recorded when returned. Unknown or incomplete cost remains null with a reason. At most one technical retry per affected operation. |
| Recovery | Completed cases are skipped. Interrupted cases are preserved without automatic regeneration and reported separately within the original denominator. |
| Review | Samples hide the condition, and an initially empty review CSV supports an actual student audit. No independent expert validation is claimed. |
| RQ4 | An experimental comparison of baseline and guardrailed conditions. Other AI/IaC tools may be compared through the literature in terms of capabilities, without an empirical performance ranking. |

There are 34 applicable intent requirements in one-spoke scenarios and 56 in two-spoke scenarios. The exact NSG rule set counts as one requirement per spoke. The overall requirement score uses equally weighted scenario-level means. The primary strict-success denominator is 24 per condition; coverage and technical blockers are reported separately.

Creating code and fixtures does not establish that the experimental research has been completed. The dissertation should report the actual date, model identifier, measurements, incomplete cases and AI assistance used, in accordance with programme requirements.

Release 1.1.0, dated 25 October 2026, adds Python 3.15 while retaining Python 3.12 support, the same pinned runtime dependencies and the same benchmark and rubric. Record the Python version from the actual environment and keep it unchanged throughout collection. Any change requires new verification, pilot and freeze stages. Old and new main data must not be pooled as though they came from the same experiment.

## Transition to Gemini: release 1.2.0

The main continuation uses `provider=gemini` and defaults to `gemini-3.8-flash`, with low thinking effort and JSON MIME output. The native generateContent API maps the same initial system/user prompt to systemInstruction/contents. The initial prompt remains common to both conditions. No separate LLM is used for guardrails or evaluation, and there is no fallback.

Repairs now retain the full case history and preserve native content parts and thought signatures unchanged, as required by the Gemini API. The 48 cases, 34/56 intent requirements, common evaluator, two patterns and two-repair limit remain the same. Provider or model effects are not assessed as a second experimental factor: all main data in the new collection come from the same frozen Gemini configuration.

Failed OpenAI trials remain earlier development or diagnostic records. They do not count as Gemini main observations. The new release requires fresh verification and actual Gemini pilots. Raw Gemini usageMetadata and the served modelVersion are retained. The workflow includes explicit pacing, reported separately alongside API transport seconds.

Free Tier access and student or Cloud credits are properties of the actual API project and require account evidence; they cannot be inferred from the 5 TB subscription. Cost remains unknown without verified billing information. Timing estimates are based only on four completed development cases without technical failures.

A published stable Gemini model identifier does not guarantee an unchanged inference infrastructure. The actual modelVersion is recorded for every response, while server defaults and non-deterministic changes remain limitations on reproducibility.
