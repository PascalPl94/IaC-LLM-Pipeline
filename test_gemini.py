import json

import httpx
import pytest

from iac_experiments.api_probe import check_api
from iac_experiments.api_support import api_key, http_error
from iac_experiments.gemini import normalize_response
from iac_experiments.models import ExperimentConfig
from iac_experiments.provider import generate, request_payload
from iac_experiments.storage import load


@pytest.fixture
def gemini_config():
    return ExperimentConfig(
        provider="gemini", model="gemini-3.8-flash", reasoning_effort="low", technical_retries=0
    )


def envelope(text='{"ok": true}'):
    return {
        "responseId": "gemini-response-test",
        "modelVersion": "gemini-3.8-flash",
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "internal summary", "thought": True}, {"text": text}],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "thoughtsTokenCount": 7,
            "cachedContentTokenCount": 2,
            "totalTokenCount": 22,
        },
    }


def install_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        "iac_experiments.gemini.httpx.Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test-secret-must-never-appear-in-evidence")


def assert_no_secret(directory):
    for path in directory.rglob("*"):
        if path.is_file():
            assert "AIza-test-secret-must-never-appear-in-evidence" not in path.read_text()


def test_native_payload_preserves_initial_and_repair_roles(gemini_config):
    messages = [
        {"role": "system", "content": "system JSON"},
        {"role": "user", "content": "scenario"},
        {"role": "assistant", "content": '{"status":"ok"}'},
        {"role": "user", "content": "repair feedback"},
    ]
    payload = request_payload(messages, gemini_config, 1)
    assert payload["systemInstruction"] == {"parts": [{"text": "system JSON"}]}
    assert [row["role"] for row in payload["contents"]] == ["user", "model", "user"]
    assert payload["contents"][1]["parts"][0]["text"] == messages[2]["content"]
    assert payload["generationConfig"] == {
        "maxOutputTokens": 12000,
        "candidateCount": 1,
        "responseMimeType": "application/json",
        "thinkingConfig": {"thinkingLevel": "LOW"},
    }
    assert not {"store", "n", "max_completion_tokens", "response_format"} & payload.keys()


def test_thought_signatures_are_replayed_exactly(gemini_config):
    previous = envelope()
    previous["candidates"][0]["content"]["parts"][-1]["thoughtSignature"] = "opaque-signature"
    normalized = normalize_response(previous, gemini_config)
    payload = request_payload(
        [
            {"role": "user", "content": "first request"},
            {
                "role": "assistant",
                "content": normalized["text"],
                "gemini_parts": normalized["assistant_parts"],
            },
            {"role": "user", "content": "repair feedback"},
        ],
        gemini_config,
        1,
    )
    assert payload["contents"][1]["parts"] == previous["candidates"][0]["content"]["parts"]


def test_native_success_counts_thought_tokens_and_preserves_envelope(
    tmp_path, gemini_config, monkeypatch
):
    requests = []

    def transport(request):
        requests.append(request)
        assert request.headers["x-goog-api-key"].startswith("AIza-")
        assert "key=" not in str(request.url) and "api.openai.com" not in str(request.url)
        return httpx.Response(200, json=envelope(), headers={"x-request-id": "req-gemini"})

    install_transport(monkeypatch, transport)
    config = gemini_config.model_copy(
        update={
            "input_usd_per_million": 1.0,
            "output_usd_per_million": 2.0,
            "cached_input_usd_per_million": 0.1,
            "pricing_source": "unit-test",
            "pricing_date": "2026-10-25",
        }
    )
    result = generate([{"role": "user", "content": "Return JSON"}], config, 1, tmp_path / "response")
    assert len(requests) == 1
    assert result["status"] == "received" and result["text"] == '{"ok": true}'
    assert result["usage"]["completion_tokens"] == 12
    assert result["usage"]["completion_tokens_details"]["reasoning_tokens"] == 7
    assert result["cost_usd_estimate"] == pytest.approx(32.2 / 1_000_000)
    assert load(tmp_path / "response/provider_response.json") == envelope()
    assert result["served_model"] == "gemini-3.8-flash"
    assert_no_secret(tmp_path)


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": 400, "message": "unsupported field", "status": "INVALID_ARGUMENT"}},
        [{"error": {"code": 400, "message": "unsupported field", "status": "INVALID_ARGUMENT"}}],
        {"error": "unsupported field"},
        "<html>unsupported field</html>",
    ],
)
def test_nonstandard_error_bodies_have_diagnostics_and_no_retry(
    tmp_path, gemini_config, monkeypatch, body
):
    requests = []

    def transport(request):
        requests.append(request)
        if isinstance(body, str):
            return httpx.Response(400, text=body)
        return httpx.Response(400, json=body)

    install_transport(monkeypatch, transport)
    result = generate(
        [{"role": "user", "content": "JSON"}],
        gemini_config.model_copy(update={"technical_retries": 1}),
        1,
        tmp_path / "response",
    )
    assert result["status"] == "technical_failure" and len(requests) == 1
    assert "unsupported field" in result["calls"][0]["error"]["provider_message"]
    assert_no_secret(tmp_path)


def test_daily_quota_is_not_retried(tmp_path, gemini_config, monkeypatch):
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "Quota exhausted",
                    "details": [
                        {
                            "violations": [
                                {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
                            ]
                        },
                        {"retryDelay": "15s"},
                    ],
                }
            },
        )

    install_transport(monkeypatch, transport)
    result = generate(
        [{"role": "user", "content": "JSON"}],
        gemini_config.model_copy(update={"technical_retries": 1}),
        1,
        tmp_path / "response",
    )
    assert len(requests) == 1
    assert result["calls"][0]["error"]["quota_or_billing_blocked"]


def test_transient_retry_is_identical_and_respects_server_delay(tmp_path, gemini_config, monkeypatch):
    requests, sleeps = [], []

    def transport(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "message": "Too many requests per minute",
                        "details": [{"retryDelay": "3s"}],
                    }
                },
                headers={"retry-after": "2"},
            )
        return httpx.Response(200, json=envelope())

    install_transport(monkeypatch, transport)
    monkeypatch.setattr("iac_experiments.api_support.time.sleep", sleeps.append)
    result = generate(
        [{"role": "user", "content": "JSON"}],
        gemini_config.model_copy(update={"technical_retries": 1}),
        1,
        tmp_path / "response",
    )
    assert result["status"] == "received" and requests[0] == requests[1]
    assert len(requests) == 2 and sleeps == [3.0]
    assert result["cost_usd_estimate"] is None


def test_excessive_retry_delay_is_retained_without_waiting(tmp_path, gemini_config, monkeypatch):
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(503, text="Unavailable", headers={"retry-after": "900"})

    install_transport(monkeypatch, transport)
    monkeypatch.setattr(
        "iac_experiments.api_support.time.sleep",
        lambda _: pytest.fail("must not wait beyond configured retry bound"),
    )
    result = generate(
        [{"role": "user", "content": "JSON"}],
        gemini_config.model_copy(update={"technical_retries": 1}),
        1,
        tmp_path / "response",
    )
    assert len(requests) == 1 and result["status"] == "technical_failure"
    assert result["calls"][0]["error"]["retry_after_seconds"] == 900


def test_malformed_http_success_is_technical_failure(tmp_path, gemini_config, monkeypatch):
    install_transport(monkeypatch, lambda _: httpx.Response(200, text="not a JSON envelope"))
    result = generate([{"role": "user", "content": "JSON"}], gemini_config, 1, tmp_path / "response")
    assert result["status"] == "technical_failure"
    assert result["calls"][0]["error"]["type"] == "InvalidProviderResponse"


def test_safety_block_is_received_refusal_not_transport_failure(gemini_config):
    result = normalize_response({"promptFeedback": {"blockReason": "SAFETY"}}, gemini_config)
    assert result["refusal"] == "SAFETY" and result["text"] == ""
    result = normalize_response(
        {
            **envelope(),
            "candidates": [
                {"content": {"parts": [{"text": '{"incomplete":'}]}, "finishReason": "MAX_TOKENS"}
            ],
        },
        gemini_config,
    )
    assert result["finish_reason"] == "length" and result["refusal"] is None


def test_error_redaction_before_truncation():
    key = "AIza-test-secret-must-never-appear-in-evidence"
    body = {"error": {"message": key + " " + "x" * 6000}}
    error = http_error(400, body, json.dumps(body), {}, key, "TestError")
    assert key not in json.dumps(error) and "[REDACTED]" in error["provider_message"]


def test_prompt_uses_gemini_key_without_shell_export(gemini_config, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-wrong-provider")
    monkeypatch.setattr("iac_experiments.api_support.sys.stdin.isatty", lambda: True)
    prompts = []

    def hidden(prompt):
        prompts.append(prompt)
        return "AIza-test-secret-must-never-appear-in-evidence"

    monkeypatch.setattr("iac_experiments.api_support.getpass.getpass", hidden)
    assert api_key(gemini_config, interactive=True).startswith("AIza-")
    assert len(prompts) == 1 and "Gemini" in prompts[0]


def test_api_probe_is_separate_and_validates_real_json_value(tmp_path, gemini_config, monkeypatch):
    install_transport(monkeypatch, lambda _: httpx.Response(200, json=envelope()))
    result = check_api(gemini_config, tmp_path / "probe")
    assert result["api_ready"] and result["data_origin"] == "api_check_only"
    assert not (tmp_path / "probe/cases").exists()
    assert_no_secret(tmp_path)


def test_cli_initializes_gemini_and_prompts_without_environment_exports(tmp_path, monkeypatch, capsys):
    from iac_experiments.cli import main

    assert main(["init", "--directory", str(tmp_path)]) == 0
    capsys.readouterr()
    configured = load(tmp_path / "config.json")
    assert configured["provider"] == "gemini" and configured["model"] == "gemini-3.8-flash"
    install_transport(monkeypatch, lambda _: httpx.Response(200, json=envelope()))
    monkeypatch.setattr("iac_experiments.api_support.sys.stdin.isatty", lambda: True)
    prompts = []

    def hidden(prompt):
        prompts.append(prompt)
        return "AIza-test-secret-must-never-appear-in-evidence"

    monkeypatch.setattr("iac_experiments.api_support.getpass.getpass", hidden)
    assert (
        main(
            [
                "check-api",
                "--config",
                str(tmp_path / "config.json"),
                "--out",
                str(tmp_path / "probe"),
                "--prompt-key",
            ]
        )
        == 0
    )
    shown = capsys.readouterr().out
    assert len(prompts) == 1 and '"api_ready": true' in shown
    assert "AIza-test-secret-must-never-appear-in-evidence" not in shown


@pytest.mark.parametrize("effort", ["none", "minimal"])
def test_unsupported_38_thinking_setting_is_rejected_before_api(effort):
    with pytest.raises(ValueError, match="Thinking|thinking"):
        ExperimentConfig(provider="gemini", model="gemini-3.8-flash", reasoning_effort=effort)


def test_pacing_applies_across_generation_calls(gemini_config, monkeypatch):
    from iac_experiments import api_support

    clock, sleeps = [100.0], []

    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr(api_support, "_LAST_STARTED", {})
    monkeypatch.setattr(api_support.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(api_support.time, "sleep", sleep)
    config = gemini_config.model_copy(update={"min_request_interval_seconds": 15.0})
    assert api_support.pace(config) == 0
    clock[0] += 2
    assert api_support.pace(config) == 13
    assert sleeps == [13.0]
