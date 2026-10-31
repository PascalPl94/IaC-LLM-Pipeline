import json

import httpx
import openai

from iac_experiments.provider import generate


def test_sdk_retries_disabled_identical_explicit_retry_no_secret_logs(tmp_path, config, monkeypatch):
    original_client = openai.OpenAI
    requests = []

    def transport(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(429, json={"error": {"message": "test", "type": "rate_limit_error"}})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "{}", "refusal": None},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        )

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        return original_client(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport))
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-that-must-not-be-written")
    monkeypatch.setattr("iac_experiments.provider.openai.OpenAI", factory)
    monkeypatch.setattr("iac_experiments.provider.time.sleep", lambda _: None)
    cfg = config.model_copy(update={"technical_retries": 1})
    result = generate([{"role": "system", "content": "Return JSON"}], cfg, 1, tmp_path / "response")
    assert result["status"] == "received" and len(result["calls"]) == 2
    assert requests[0] == requests[1]
    assert result["usage"]["total_tokens"] == 6
    assert result["cost_usd_estimate"] is None
    assert all(
        "test-secret-that-must-not-be-written" not in p.read_text()
        for p in (tmp_path / "response").iterdir()
    )


def test_api_configuration_error_is_not_retried(tmp_path, config, monkeypatch):
    original_client = openai.OpenAI
    count = []

    def transport(request):
        count.append(1)
        return httpx.Response(
            400, json={"error": {"message": "unsupported setting", "type": "invalid_request_error"}}
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(
        "iac_experiments.provider.openai.OpenAI",
        lambda **kwargs: original_client(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport))
        ),
    )
    result = generate(
        [{"role": "system", "content": "JSON"}],
        config.model_copy(update={"technical_retries": 1}),
        1,
        tmp_path / "response",
    )
    assert result["status"] == "technical_failure" and len(count) == 1
