"""Tests for the api_logger token-breakdown estimate (issue #17).

Providers like ollama-cloud return only aggregate ``prompt_tokens``; the logger
must fill a local tools/system/messages estimate so ``request_tokens`` is not
all-zero, and must capture the tool schemas in the request log.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import api_logger


class _FakeAgent:
    def __init__(self, logs_dir: Path):
        self._api_request_logging = "redacted"
        self._api_request_logging_errors_only = False
        self._api_token_metrics_logging = True
        self.logs_dir = logs_dir
        self.session_id = "sess-test"
        self.turn_id = "turn-1"
        self._api_call_count = 1


def _read_jsonl(path: Path):
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _big_tools(n: int = 6):
    return [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "A tool with a reasonably verbose description " * 5,
                "parameters": {
                    "type": "object",
                    "properties": {"arg": {"type": "string", "description": "x" * 40}},
                },
            },
        }
        for i in range(n)
    ]


def test_estimate_request_token_breakdown_populates_tools_system_messages():
    payload = {
        "tools": _big_tools(),
        "system": "You are a helpful agent. " * 20,
        "messages": [{"role": "user", "content": "Hello"}],
    }
    est = api_logger._estimate_request_token_breakdown(payload)
    assert est["tools_tokens"] > 0
    assert est["system_tokens"] > 0
    assert est["messages_tokens"] > 0
    # Tools dominate a bare "Hello" turn — the whole point of the issue.
    assert est["tools_tokens"] > est["messages_tokens"]


def test_estimate_handles_body_nesting():
    payload = {"body": {"tools": _big_tools(), "system": "system prompt text " * 10}}
    est = api_logger._estimate_request_token_breakdown(payload)
    assert est["tools_tokens"] > 0
    assert est["system_tokens"] > 0


def test_log_token_metrics_fills_zero_breakdown(tmp_path):
    agent = _FakeAgent(tmp_path)
    # Provider usage returns only the aggregate prompt_tokens (ollama-cloud shape).
    usage = SimpleNamespace(prompt_tokens=20524, input_tokens=20524, output_tokens=5)
    payload = {
        "tools": _big_tools(),
        "system": "You are a helpful agent. " * 20,
        "messages": [{"role": "user", "content": "Hello"}],
    }
    api_logger.log_token_metrics(
        agent=agent,
        usage=usage,
        model="gemma3:12b",
        provider="ollama",
        cost_usd=None,
        duration_ms=100.0,
        request_payload=payload,
    )
    entries = _read_jsonl(tmp_path / api_logger._get_session_log_path(agent, "metrics").name)
    assert len(entries) == 1
    rt = entries[0]["request_tokens"]
    assert rt["tools_tokens"] > 0
    assert rt["system_tokens"] > 0
    assert rt["messages_tokens"] > 0
    assert set(entries[0]["request_tokens_estimated"]) == {
        "tools_tokens",
        "system_tokens",
        "messages_tokens",
    }


def test_log_token_metrics_does_not_override_provider_values(tmp_path):
    agent = _FakeAgent(tmp_path)
    # Provider DID supply a real tools_tokens — estimate must not clobber it.
    usage = SimpleNamespace(prompt_tokens=100, tools_tokens=999)
    payload = {"tools": _big_tools()}
    api_logger.log_token_metrics(
        agent=agent,
        usage=usage,
        model="m",
        provider="p",
        cost_usd=None,
        duration_ms=1.0,
        request_payload=payload,
    )
    entries = _read_jsonl(tmp_path / api_logger._get_session_log_path(agent, "metrics").name)
    assert entries[0]["request_tokens"]["tools_tokens"] == 999
    assert "tools_tokens" not in entries[0]["request_tokens_estimated"]


def test_log_api_request_captures_tools(tmp_path):
    agent = _FakeAgent(tmp_path)
    payload = {
        "url": "https://example/v1/messages",
        "tools": _big_tools(),
        "system": "sys prompt",
        "messages": [{"role": "user", "content": "Hello"}],
    }
    api_logger.log_api_request(
        agent=agent,
        request_payload=payload,
        response=None,
        duration_ms=1.0,
    )
    entries = _read_jsonl(tmp_path / api_logger._get_session_log_path(agent, "requests").name)
    assert len(entries) == 1
    req = entries[0]["request"]
    assert req["tools"] is not None
    assert len(req["tools"]) == 6
    assert req["system"] == "sys prompt"
