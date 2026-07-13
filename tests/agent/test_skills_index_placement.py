"""Tests for skill-index placement as a user-role message (issue #17).

The compact skill index defaults to a call-time user message rather than being
baked into the cached system prompt, so the system prefix stays lean and the
index can be gated per turn. The Anthropic adapter must still see valid
user/assistant alternation after injection.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent import conversation_loop
from agent import system_prompt as sp


def _agent(content="## Available Skills\n- github: PR workflows\n- notion: notes"):
    return SimpleNamespace(_skills_index_content=content)


class TestBuildSkillsIndexMessage:
    def test_none_when_no_content(self):
        assert conversation_loop._build_skills_index_message(_agent(""), "hi") is None
        assert conversation_loop._build_skills_index_message(SimpleNamespace(), "hi") is None

    def test_user_message_by_default(self, monkeypatch):
        monkeypatch.setattr(sp, "_skills_index_placement", lambda: "user_message")
        monkeypatch.setattr(sp, "_skills_index_complexity_gating", lambda: False)
        msg = conversation_loop._build_skills_index_message(_agent(), "do a thing")
        assert msg["role"] == "user"
        assert "Available Skills" in msg["content"]

    def test_none_when_placement_is_system(self, monkeypatch):
        monkeypatch.setattr(sp, "_skills_index_placement", lambda: "system")
        assert conversation_loop._build_skills_index_message(_agent(), "do a thing") is None

    def test_complexity_gating_skips_simple(self, monkeypatch):
        monkeypatch.setattr(sp, "_skills_index_placement", lambda: "user_message")
        monkeypatch.setattr(sp, "_skills_index_complexity_gating", lambda: True)
        # A terse greeting classifies as "simple" -> index dropped.
        assert conversation_loop._build_skills_index_message(_agent(), "hi") is None

    def test_complexity_gating_keeps_complex(self, monkeypatch):
        monkeypatch.setattr(sp, "_skills_index_placement", lambda: "user_message")
        monkeypatch.setattr(sp, "_skills_index_complexity_gating", lambda: True)
        complex_msg = (
            "First research the codebase, then implement a multi-step pipeline "
            "that refactors the module, and after that build and analyze the results."
        )
        msg = conversation_loop._build_skills_index_message(_agent(), complex_msg)
        assert msg is not None and msg["role"] == "user"


class TestAlternationSafety:
    def test_injected_user_message_merges_with_next_user_turn(self):
        """system + injected-skills-user + real-user must not break Anthropic
        alternation — the adapter merges the two consecutive user messages."""
        from agent.anthropic_adapter import convert_messages_to_anthropic

        messages = [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "## Available Skills\n- github"},
            {"role": "user", "content": "Open a PR"},
            {"role": "assistant", "content": "Sure."},
            {"role": "user", "content": "Thanks"},
        ]
        system, conv = convert_messages_to_anthropic(messages)
        assert system == "SYS"
        roles = [m["role"] for m in conv]
        # No two consecutive same-role messages (valid alternation).
        assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), roles
        # The skills text survived, merged into the first user turn.
        flat = str(conv[0]["content"])
        assert "Available Skills" in flat and "Open a PR" in flat


class TestPlacementConfigDefault:
    def test_placement_defaults_to_user_message(self):
        # With an isolated (empty) config, the default must be user_message.
        assert sp._skills_index_placement() == "user_message"

    def test_gating_defaults_off(self):
        assert sp._skills_index_complexity_gating() is False
