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
