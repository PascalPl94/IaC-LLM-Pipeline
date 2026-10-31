# Gemini: student subscription, API access and limits

Official documentation reviewed on 25 October 2026.

Google AI Pro with 5 TB is a subscription that provides benefits for specific products. The Python experiments require a **Gemini API key** from Google AI Studio and an API project. The application does not use cookies, browser login or unofficial access to the Gemini app.

## Using the Free Tier without purchasing credits

At https://aistudio.google.com/apikey, use a project listed as **Free Tier** and create an API key. Selected Free Tier models offer input and output without charge, subject to request and token limits. The package selects the stable `gemini-3.8-flash` model, for which the published pricing lists a Free Tier. Confirm access and active limits in your own account.

Actual billing depends on the project associated with the key. The same application can use a Free Tier project or a paid project backed by credits. A successful generateContent response does not allow the code to determine the billing tier or remaining Cloud credits. The `check-api` command confirms that the particular API operation works; it does not establish that usage is free or that the quota will last for the full day.

## Benefits that an AI Pro subscription may provide

The benefits page reviewed for this release lists Google Developer Program premium and **$10 in Google Cloud credits each month** for Google AI Pro. An active Pro membership must be linked to the Google Developer profile. Holding a student subscription does not establish that the credits have been activated, redeemed or applied to the API project in use.

If the benefit appears at https://developers.google.com/profile, check its activation instructions and the Cloud Billing account to which it applies. Under the Gemini Prepay instructions reviewed for this release, an initial prepayment may be required before eligible Cloud credits can be used. This differs from using the Free Tier without making a purchase.

General Google Cloud Welcome/Free Trial credits should be distinguished from eligible developer credits: the Gemini API rules reviewed for this release treat them differently. Confirm which benefit is actually available in your account before describing it as funding for the experiments.

## Quota needed for one day of collection

Active limits appear in Google AI Studio. Check all three: **RPM** (requests per minute), **TPM** (tokens per minute) and **RPD** (requests per day). RPD resets at midnight Pacific time, rather than midnight in Greece. Limits apply per project and model; creating a new API key does not increase the quota.

| Stage | Cases | Logical calls | Maximum transport attempts |
|---|---:|---:|---:|
| One API check | Outside the benchmark | 1 | 2 |
| Development pilot | 4 | 4–8 | 16 |
| Main | 48 | 48–96 | 192 |
| Total including one check | 52 + check | 53–105 | 210 |

These maxima include up to two quality repairs per guardrailed case and up to one technical retry with the same payload for each affected call. Additional checks or new pilots consume further quota. The service determines how failed requests count towards quota, so allow a margin.

If the actual daily quota is below 53 requests, the complete plan, including the pilot and API check, will not fit within that quota even without repairs. The upper bound of 105 logical requests covers the full quality-repair allowance before technical retries. Storage capacity or a subscription described as free cannot guarantee completion in one day.

The supplied configuration sets a minimum interval of 15 seconds between request starts. Actual waiting time and server retry hints are recorded. Pacing reduces the request rate; it does not increase the daily quota or guarantee compliance with the TPM limit.

When a 429 response indicates that the daily quota is exhausted, the application preserves the error and stops. It does not switch models, projects or providers, or bypass the limits. If different model settings are needed, decide on them using the DEV inputs, then complete new verification, pilot and freeze stages before the main collection. Do not change the configuration during a frozen experiment.

## Recording monetary cost

Native usage counters are retained in full. For comparable output-token reporting, visible candidate tokens and thinking tokens are added together. Cached input tokens remain separate. Logs retain both the requested model identifier and the `modelVersion` that served the request.

Cost values are initially unknown (`null`) because the application cannot verify your personal billing or credit arrangements. A zero value is appropriate only when actual use without charge has been documented. Add verified prices, their source and date to the configuration before a new verify/freeze sequence. Check the final charge in Google AI Studio or Cloud Billing.

## Official sources

- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/rate-limits
- https://ai.google.dev/gemini-api/docs/billing
- https://support.google.com/googleone/answer/14534406?hl=en
- https://developers.google.com/profile/help/faq
- https://developers.google.com/profile/help/benefits
- https://ai.google.dev/api/generate-content
