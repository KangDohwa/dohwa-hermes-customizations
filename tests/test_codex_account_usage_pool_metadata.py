import base64
import json
from types import SimpleNamespace

import pytest

from agent import account_usage


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, calls, payload):
        self.calls = calls
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers):
        self.calls.append({"url": url, "headers": headers})
        return _FakeResponse(self.payload)


def _jwt(account_id):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return ".".join(
        (
            encode({"alg": "none"}),
            encode({"https://api.openai.com/auth": {"chatgpt_account_id": account_id}}),
            "signature",
        )
    )


@pytest.fixture
def usage_payload():
    return {
        "plan_type": "plus",
        "rate_limit": {
            "primary_window": {"used_percent": 21, "reset_at": 1779846359},
            "secondary_window": {"used_percent": 4, "reset_at": 1780230796},
        },
        "credits": {"has_credits": False},
    }


def test_explicit_codex_jwt_sends_its_account_id(monkeypatch, usage_payload):
    calls = []
    token = _jwt("acct-live")
    monkeypatch.setattr(
        account_usage.httpx,
        "Client",
        lambda timeout: _FakeClient(calls, usage_payload),
    )
    monkeypatch.setattr(
        account_usage,
        "resolve_codex_runtime_credentials",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("resolver must not run")),
    )

    snapshot = account_usage.fetch_account_usage(
        "openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        api_key=token,
    )

    assert snapshot is not None
    assert calls[0]["headers"]["Authorization"] == f"Bearer {token}"
    assert calls[0]["headers"]["ChatGPT-Account-Id"] == "acct-live"


def test_runtime_pool_reselects_one_usable_entry_for_token_and_account(
    monkeypatch, usage_payload
):
    calls = []
    stale_token = _jwt("acct-stale")
    selected_token = _jwt("acct-selected")
    monkeypatch.setattr(
        account_usage.httpx,
        "Client",
        lambda timeout: _FakeClient(calls, usage_payload),
    )
    monkeypatch.setattr(
        account_usage,
        "resolve_codex_runtime_credentials",
        lambda **kwargs: {
            "source": "credential_pool",
            "api_key": stale_token,
            "base_url": "https://chatgpt.com/backend-api/codex",
        },
    )
    monkeypatch.setattr(
        account_usage,
        "_read_codex_tokens",
        lambda: {"tokens": {"account_id": "acct-singleton"}},
    )

    import agent.credential_pool as credential_pool

    selected = SimpleNamespace(
        runtime_api_key=selected_token,
        runtime_base_url="https://chatgpt.com/backend-api/codex",
    )
    monkeypatch.setattr(
        credential_pool,
        "load_pool",
        lambda provider: SimpleNamespace(select=lambda: selected),
    )

    snapshot = account_usage.fetch_account_usage("openai-codex")

    assert snapshot is not None
    assert calls[0]["headers"]["Authorization"] == f"Bearer {selected_token}"
    assert calls[0]["headers"]["ChatGPT-Account-Id"] == "acct-selected"


def test_runtime_pool_opaque_token_ignores_singleton_account_id(
    monkeypatch, usage_payload
):
    calls = []
    monkeypatch.setattr(
        account_usage.httpx,
        "Client",
        lambda timeout: _FakeClient(calls, usage_payload),
    )
    monkeypatch.setattr(
        account_usage,
        "resolve_codex_runtime_credentials",
        lambda **kwargs: {
            "source": "credential_pool",
            "api_key": "opaque-stale-token",
            "base_url": "https://chatgpt.com/backend-api/codex",
        },
    )
    monkeypatch.setattr(
        account_usage,
        "_read_codex_tokens",
        lambda: {"tokens": {"account_id": "acct-singleton"}},
    )

    import agent.credential_pool as credential_pool

    selected = SimpleNamespace(
        runtime_api_key="opaque-selected-token",
        runtime_base_url="https://chatgpt.com/backend-api/codex",
    )
    monkeypatch.setattr(
        credential_pool,
        "load_pool",
        lambda provider: SimpleNamespace(select=lambda: selected),
    )

    snapshot = account_usage.fetch_account_usage("openai-codex")

    assert snapshot is not None
    assert calls[0]["headers"]["Authorization"] == "Bearer opaque-selected-token"
    assert "ChatGPT-Account-Id" not in calls[0]["headers"]


def test_direct_pool_fallback_uses_selected_jwt_account_id(monkeypatch, usage_payload):
    calls = []
    token = _jwt("acct-pool")
    monkeypatch.setattr(
        account_usage.httpx,
        "Client",
        lambda timeout: _FakeClient(calls, usage_payload),
    )
    monkeypatch.setattr(
        account_usage,
        "resolve_codex_runtime_credentials",
        lambda **kwargs: (_ for _ in ()).throw(
            account_usage.AuthError(
                "missing", provider="openai-codex", code="codex_auth_missing"
            )
        ),
    )

    import agent.credential_pool as credential_pool

    selected = SimpleNamespace(
        runtime_api_key=token,
        runtime_base_url="https://chatgpt.com/backend-api/codex",
    )
    monkeypatch.setattr(
        credential_pool,
        "load_pool",
        lambda provider: SimpleNamespace(select=lambda: selected),
    )

    snapshot = account_usage.fetch_account_usage("openai-codex")

    assert snapshot is not None
    assert calls[0]["headers"]["Authorization"] == f"Bearer {token}"
    assert calls[0]["headers"]["ChatGPT-Account-Id"] == "acct-pool"
