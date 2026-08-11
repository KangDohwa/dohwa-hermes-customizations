"""Regression tests for explicit web-search backend fail-closed behavior."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


if Path(__file__).parent.name != "tools":
    pytest.skip(
        "run against the patched upstream worktree via scripts/verify.sh",
        allow_module_level=True,
    )


def _paid_provider():
    provider = MagicMock()
    provider.name = "tavily"
    provider.supports_search.return_value = True
    provider.search.return_value = {"success": True, "data": {"web": []}}
    return provider


def test_explicit_unavailable_search_backend_does_not_dispatch_paid_fallback(monkeypatch):
    from agent import web_search_registry
    from tools import web_tools

    paid = _paid_provider()
    monkeypatch.setattr(
        web_tools, "_load_web_config", lambda: {"search_backend": "ddgs"}
    )
    monkeypatch.setattr(web_tools, "_is_backend_available", lambda _name: False)
    monkeypatch.setattr(web_tools, "_get_backend", lambda: "tavily")
    monkeypatch.setattr(web_tools, "_ensure_web_plugins_loaded", lambda: None)
    monkeypatch.setattr(
        web_search_registry,
        "get_provider",
        lambda name: paid if name == "tavily" else None,
    )
    monkeypatch.setattr(
        web_search_registry, "get_active_search_provider", lambda: paid
    )
    monkeypatch.setattr(
        web_search_registry, "_disabled_web_plugin_for", lambda **_kwargs: None
    )
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)
    monkeypatch.setattr(web_tools._debug, "log_call", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_tools._debug, "save", lambda: None)

    result = json.loads(web_tools.web_search_tool("offline probe", limit=1))

    paid.search.assert_not_called()
    assert result["success"] is False


@pytest.mark.asyncio
async def test_explicit_unavailable_extract_backend_does_not_dispatch_fallback(monkeypatch):
    from agent import web_search_registry
    from tools import web_tools

    fallback = MagicMock()
    fallback.name = "tavily"
    fallback.supports_extract.return_value = True
    fallback.extract.return_value = []

    monkeypatch.setattr(
        web_tools, "_load_web_config", lambda: {"extract_backend": "exa"}
    )
    monkeypatch.setattr(web_tools, "_is_backend_available", lambda _name: False)
    monkeypatch.setattr(web_tools, "_get_backend", lambda: "tavily")
    monkeypatch.setattr(web_tools, "_ensure_web_plugins_loaded", lambda: None)
    monkeypatch.setattr(
        web_search_registry,
        "get_provider",
        lambda name: fallback if name == "tavily" else None,
    )
    monkeypatch.setattr(
        web_search_registry, "get_active_extract_provider", lambda: fallback
    )
    monkeypatch.setattr(
        web_search_registry, "_disabled_web_plugin_for", lambda **_kwargs: None
    )

    async def safe_url(_url):
        return True

    monkeypatch.setattr(web_tools, "async_is_safe_url", safe_url)
    monkeypatch.setattr(web_tools._debug, "log_call", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_tools._debug, "save", lambda: None)

    result = json.loads(
        await web_tools.web_extract_tool(["https://example.com/article"])
    )

    fallback.extract.assert_not_called()
    assert result["success"] is False
    assert "extract_backend" in result["error"]


def _configure_search_pair(monkeypatch, primary_response):
    from agent import web_search_registry
    from tools import web_tools

    primary = MagicMock()
    primary.name = "tavily"
    primary.supports_search.return_value = True
    primary.search.return_value = primary_response

    fallback = MagicMock()
    fallback.name = "ddgs"
    fallback.display_name = "DDGS"
    fallback.supports_search.return_value = True
    fallback.is_available.return_value = True
    fallback.search.return_value = {
        "success": True,
        "data": {
            "web": [
                {
                    "title": "fallback result",
                    "url": "https://example.com/fallback",
                    "description": "from ddgs",
                }
            ]
        },
    }

    monkeypatch.setattr(
        web_tools,
        "_load_web_config",
        lambda: {
            "search_backend": "tavily",
            "search_fallback_backend": "ddgs",
        },
    )
    monkeypatch.setattr(web_tools, "_ensure_web_plugins_loaded", lambda: None)
    monkeypatch.setattr(
        web_search_registry,
        "get_provider",
        lambda name: {"tavily": primary, "ddgs": fallback}.get(name),
    )
    active = MagicMock(name="active_provider_walk")
    monkeypatch.setattr(web_search_registry, "get_active_search_provider", active)
    monkeypatch.setattr(
        web_search_registry, "_disabled_web_plugin_for", lambda **_kwargs: None
    )
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)
    monkeypatch.setattr(web_tools._debug, "log_call", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_tools._debug, "save", lambda: None)
    return web_tools, primary, fallback, active


def test_retryable_primary_failure_uses_explicit_search_fallback(monkeypatch):
    web_tools, primary, fallback, active = _configure_search_pair(
        monkeypatch,
        {
            "success": False,
            "error": "rate limited",
            "error_code": "rate_limited",
            "retryable": True,
        },
    )

    result = json.loads(web_tools.web_search_tool("fallback probe", limit=2))

    primary.search.assert_called_once_with("fallback probe", 2)
    fallback.search.assert_called_once_with("fallback probe", 2)
    active.assert_not_called()
    assert result["success"] is True
    assert result["fallback"] == {
        "from": "tavily",
        "to": "ddgs",
        "reason": "rate_limited",
    }


def test_nonretryable_primary_failure_does_not_fallback(monkeypatch):
    web_tools, primary, fallback, active = _configure_search_pair(
        monkeypatch,
        {
            "success": False,
            "error": "unauthorized",
            "error_code": "authentication_failed",
            "retryable": False,
        },
    )

    result = json.loads(web_tools.web_search_tool("auth probe", limit=1))

    primary.search.assert_called_once_with("auth probe", 1)
    fallback.search.assert_not_called()
    active.assert_not_called()
    assert result["success"] is False
    assert result["error_code"] == "authentication_failed"


def test_empty_primary_results_use_explicit_search_fallback(monkeypatch):
    web_tools, primary, fallback, active = _configure_search_pair(
        monkeypatch,
        {"success": True, "data": {"web": []}},
    )

    result = json.loads(web_tools.web_search_tool("empty probe", limit=3))

    primary.search.assert_called_once_with("empty probe", 3)
    fallback.search.assert_called_once_with("empty probe", 3)
    active.assert_not_called()
    assert result["success"] is True
    assert result["fallback"]["reason"] == "no_results"


@pytest.mark.parametrize(
    "malformed_response",
    [
        {"success": True},
        {"success": True, "data": {}},
        {"success": True, "data": {"web": None}},
    ],
)
def test_malformed_success_response_fails_closed(monkeypatch, malformed_response):
    web_tools, primary, fallback, active = _configure_search_pair(
        monkeypatch,
        malformed_response,
    )

    result = json.loads(web_tools.web_search_tool("schema probe", limit=1))

    primary.search.assert_called_once_with("schema probe", 1)
    fallback.search.assert_not_called()
    active.assert_not_called()
    assert result["success"] is False
    assert result["error_code"] == "invalid_response"
    assert result["retryable"] is False


def test_tavily_missing_results_fails_closed(monkeypatch):
    from plugins.web.tavily import provider as tavily_provider

    monkeypatch.setattr(tavily_provider, "_tavily_request", lambda *_args: {})

    result = tavily_provider.TavilyWebSearchProvider().search("schema probe", 1)

    assert result["success"] is False
    assert result["error_code"] == "invalid_response"
    assert result["retryable"] is False


def test_unconfigured_search_keeps_active_provider_fallback(monkeypatch):
    from agent import web_search_registry
    from tools import web_tools

    paid = _paid_provider()
    monkeypatch.setattr(web_tools, "_load_web_config", lambda: {})
    monkeypatch.setattr(web_tools, "_get_backend", lambda: "missing-auto-provider")
    monkeypatch.setattr(web_tools, "_ensure_web_plugins_loaded", lambda: None)
    monkeypatch.setattr(web_search_registry, "get_provider", lambda _name: None)
    monkeypatch.setattr(
        web_search_registry, "get_active_search_provider", lambda: paid
    )
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)
    monkeypatch.setattr(web_tools._debug, "log_call", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_tools._debug, "save", lambda: None)

    result = json.loads(web_tools.web_search_tool("auto probe", limit=1))

    paid.search.assert_called_once_with("auto probe", 1)
    assert result["success"] is True


@pytest.mark.parametrize(
    ("status", "error_code", "retryable"),
    [
        (401, "authentication_failed", False),
        (429, "rate_limited", True),
        (503, "provider_unavailable", True),
    ],
)
def test_tavily_http_error_retryability(status, error_code, retryable):
    import httpx

    from plugins.web.tavily.provider import _tavily_error_response

    request = httpx.Request("POST", "https://api.tavily.com/search")
    response = httpx.Response(status, request=request)
    error = httpx.HTTPStatusError(
        f"status {status}", request=request, response=response
    )

    result = _tavily_error_response(error, operation="search")

    assert result["success"] is False
    assert result["error_code"] == error_code
    assert result["retryable"] is retryable


def test_tavily_timeout_is_retryable():
    import httpx

    from plugins.web.tavily.provider import _tavily_error_response

    request = httpx.Request("POST", "https://api.tavily.com/search")
    result = _tavily_error_response(
        httpx.ReadTimeout("timed out", request=request), operation="search"
    )

    assert result["error_code"] == "timeout"
    assert result["retryable"] is True
