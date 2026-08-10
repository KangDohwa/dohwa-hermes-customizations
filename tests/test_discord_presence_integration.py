"""Integration contracts for the Discord dynamic-presence semantic rebase."""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.config import PlatformConfig
from gateway.run import GatewayRunner, TurnRunner
from gateway.turn_context import TurnContext
from plugins.platforms.discord.adapter import DiscordAdapter


class _PresenceAdapter:
    def __init__(self):
        self.events = []

    def presence_tool_started(self, session_key, generation, call_id):
        self.events.append(("start", session_key, generation, call_id))

    def presence_tool_finished(self, session_key, generation, call_id):
        self.events.append(("finish", session_key, generation, call_id))


class _Runner:
    def __init__(self, adapter):
        self.adapter = adapter
        self.adapters = {}

    def _adapter_for_source(self, source):
        return self.adapter


def test_outer_turn_presence_uses_generation_and_existing_finally():
    source = inspect.getsource(GatewayRunner._handle_message)
    generation = source.index("_run_generation = self._begin_session_run_generation(_quick_key)")
    started = source.index("presence_turn_started(\n                    _quick_key, _run_generation")
    agent_call = source.index("_agent_result = await self._handle_message_with_agent")
    outer_finally = source.index("        finally:\n", agent_call)
    finished = source.index("presence_turn_finished(\n                        _quick_key, _run_generation", outer_finally)
    restore = source.index("self._restore_moa_one_shot", outer_finally)

    assert generation < started < agent_call < outer_finally < finished < restore


def test_turn_runner_callbacks_fan_out_presence_and_preserve_voice_ack():
    adapter = _PresenceAdapter()
    ctx = TurnContext(source=object(), session_key="discord:42", run_generation=7)
    turn = TurnRunner(_Runner(adapter), ctx)
    voice_ack = Mock()
    turn.voice_ack_callback = voice_ack

    turn.gateway_tool_start_callback("call-1", "terminal", {"command": "pwd"})
    turn.gateway_tool_complete_callback("call-1", "terminal", {}, "ok")

    assert adapter.events == [
        ("start", "discord:42", 7, "call-1"),
        ("finish", "discord:42", 7, "call-1"),
    ]
    voice_ack.assert_called_once_with("call-1", "terminal", {"command": "pwd"})


def test_turn_runner_wiring_keeps_progress_callback_separate():
    source = inspect.getsource(TurnRunner.run_sync)
    assert "agent.tool_progress_callback = (" in source
    assert "ctx.progress_callback" in source
    assert "self.gateway_tool_start_callback" in source
    assert "agent.tool_complete_callback = (" in source
    assert "self.gateway_tool_complete_callback" in source


def test_discord_adapter_owns_controller_and_delegates_turn_and_tools():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    assert type(adapter._presence).__name__ == "DiscordPresenceController"
    adapter._presence = Mock()

    adapter.presence_turn_started("s", 1)
    adapter.presence_turn_finished("s", 1)
    adapter.presence_tool_started("s", 1, "c")
    adapter.presence_tool_finished("s", 1, "c")

    adapter._presence.turn_started.assert_called_once_with("s", 1)
    adapter._presence.turn_finished.assert_called_once_with("s", 1)
    adapter._presence.tool_started.assert_called_once_with("s", 1, "c")
    adapter._presence.tool_finished.assert_called_once_with("s", 1, "c")


def test_on_ready_reasserts_presence():
    source = inspect.getsource(DiscordAdapter.connect)
    ready = source.index("async def on_ready():")
    ready_event = source.index("adapter_self._ready_event.set()", ready)
    reassert = source.index("await adapter_self._presence.reassert()", ready)
    post_connect = source.index("adapter_self._post_connect_task = asyncio.create_task", ready)
    assert ready_event < reassert < post_connect


@pytest.mark.asyncio
async def test_disconnect_stops_presence_controller():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    adapter._presence = SimpleNamespace(stop=AsyncMock())

    await adapter.disconnect()

    adapter._presence.stop.assert_awaited_once_with()


class _Channel:
    def __init__(self, error=None):
        self.error = error

    async def send(self, **kwargs):
        if self.error:
            raise self.error
        return SimpleNamespace(id=123)


class _Client:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, channel_id):
        return self.channel

    async def fetch_channel(self, channel_id):
        return self.channel


def _prompt_adapter(channel):
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    adapter._client = _Client(channel)
    adapter._presence = Mock()
    return adapter


@pytest.mark.asyncio
async def test_wait_watchers_start_only_after_successful_discord_send():
    adapter = _prompt_adapter(_Channel())

    approval = await adapter.send_exec_approval("1", "pwd", "approval-session")
    clarify = await adapter.send_clarify("1", "Which?", [], "clarify-id", "response-session")

    assert approval.success is True
    assert clarify.success is True
    adapter._presence.watch_approval.assert_called_once_with("approval-session")
    adapter._presence.watch_response.assert_called_once_with("response-session")


@pytest.mark.asyncio
async def test_failed_discord_send_does_not_start_wait_watchers():
    adapter = _prompt_adapter(_Channel(RuntimeError("offline")))

    approval = await adapter.send_exec_approval("1", "pwd", "approval-session")
    clarify = await adapter.send_clarify("1", "Which?", [], "clarify-id", "response-session")

    assert approval.success is False
    assert clarify.success is False
    adapter._presence.watch_approval.assert_not_called()
    adapter._presence.watch_response.assert_not_called()
