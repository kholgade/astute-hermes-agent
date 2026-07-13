"""Tests for the ``tools.tool_search.minimal_core`` lever (issue #17).

When enabled, only ``_HERMES_MINIMAL_CORE`` stays always-on; every other core
tool becomes deferrable and is served through the tool_search bridges instead
of costing schema tokens on every turn.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _td(name, description="d"):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


class TestConfigParsing:
    def test_minimal_core_defaults_off(self):
        from tools.tool_search import ToolSearchConfig
        assert ToolSearchConfig.from_raw(None).minimal_core is False
        assert ToolSearchConfig.from_raw(True).minimal_core is False
        assert ToolSearchConfig.from_raw({"enabled": "on"}).minimal_core is False

    def test_minimal_core_parsed_true(self):
        from tools.tool_search import ToolSearchConfig
        for val in (True, "true", "yes", "on", 1):
            cfg = ToolSearchConfig.from_raw({"enabled": "on", "minimal_core": val})
            assert cfg.minimal_core is True, val

    def test_minimal_core_parsed_false(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw({"enabled": "on", "minimal_core": "no"})
        assert cfg.minimal_core is False


class TestCoreSetSelection:
    def test_minimal_core_is_strict_subset_of_full_core(self):
        """Every minimal-core name must be a real core tool (typo guard)."""
        from toolsets import _HERMES_MINIMAL_CORE, _HERMES_CORE_TOOLS
        assert set(_HERMES_MINIMAL_CORE).issubset(set(_HERMES_CORE_TOOLS))

    def test_core_tool_names_switches_on_config(self):
        from tools.tool_search import ToolSearchConfig, _core_tool_names
        from toolsets import _HERMES_MINIMAL_CORE, _HERMES_CORE_TOOLS
        minimal = _core_tool_names(ToolSearchConfig.from_raw(
            {"enabled": "on", "minimal_core": True}))
        full = _core_tool_names(ToolSearchConfig.from_raw({"enabled": "on"}))
        assert minimal == frozenset(_HERMES_MINIMAL_CORE)
        assert full == frozenset(_HERMES_CORE_TOOLS)

    def test_minimal_core_keeps_substrate_drops_specialized(self):
        from tools.tool_search import ToolSearchConfig, _core_tool_names
        minimal = _core_tool_names(ToolSearchConfig.from_raw(
            {"enabled": "on", "minimal_core": True}))
        for kept in ("web_search", "read_file", "write_file", "terminal",
                     "execute_code", "skill_view"):
            assert kept in minimal
        for dropped in ("browser_navigate", "vision_analyze", "image_generate",
                        "memory", "todo", "clarify", "session_search"):
            assert dropped not in minimal


class TestDeferralBehavior:
    def test_minimal_core_tool_stays_non_deferrable(self):
        from tools.tool_search import ToolSearchConfig, is_deferrable_tool_name
        cfg = ToolSearchConfig.from_raw({"enabled": "on", "minimal_core": True})
        # web_search is in the minimal substrate — never deferrable, and the
        # name-gate short-circuits before any registry lookup.
        assert is_deferrable_tool_name("web_search", cfg) is False

    def test_full_core_protects_specialized_tool(self):
        from tools.tool_search import ToolSearchConfig, is_deferrable_tool_name
        full = ToolSearchConfig.from_raw({"enabled": "on"})
        # Under the full-core default, browser_navigate is name-protected even
        # without a registry entry.
        assert is_deferrable_tool_name("browser_navigate", full) is False

    def test_minimal_core_flips_registered_core_tool_to_deferrable(self):
        """The crux: a real core tool becomes deferrable under minimal_core.

        Requires a registry entry (deferral eligibility falls through to the
        registry once the name-gate is lifted), so we register a stand-in.
        """
        from tools.registry import registry
        from tools.tool_search import ToolSearchConfig, is_deferrable_tool_name

        name = "todo"
        had_entry = registry.get_entry(name) is not None
        if not had_entry:
            registry.register(
                name=name,
                toolset="todo",
                schema={"name": name, "description": "d",
                        "parameters": {"type": "object", "properties": {}}},
                handler=lambda **kw: "",
            )
        try:
            full = ToolSearchConfig.from_raw({"enabled": "on"})
            minimal = ToolSearchConfig.from_raw({"enabled": "on", "minimal_core": True})
            assert is_deferrable_tool_name(name, full) is False
            assert is_deferrable_tool_name(name, minimal) is True
        finally:
            if not had_entry:
                try:
                    registry.deregister(name)
                except Exception:
                    pass

    def test_assembly_defers_specialized_tools_under_minimal_core(self):
        """End-to-end: with registered core tools + minimal_core, the assembly
        keeps the substrate visible and hides the rest behind bridges."""
        from tools.registry import registry
        from tools.tool_search import (
            assemble_tool_defs, ToolSearchConfig, BRIDGE_TOOL_NAMES,
        )

        specialized = ["browser_navigate", "vision_analyze", "image_generate",
                       "memory", "todo", "clarify"]
        registered = []
        for n in specialized:
            if registry.get_entry(n) is None:
                registry.register(
                    name=n, toolset=n.split("_")[0],
                    schema={"name": n, "description": "verbose " * 50,
                            "parameters": {"type": "object", "properties": {}}},
                    handler=lambda **kw: "",
                )
                registered.append(n)
        try:
            defs = [_td("web_search"), _td("read_file"), _td("terminal")]
            defs += [_td(n, "verbose " * 50) for n in specialized]
            cfg = ToolSearchConfig.from_raw(
                {"enabled": "on", "minimal_core": True})
            result = assemble_tool_defs(defs, context_length=8000, config=cfg)
            assert result.activated
            names = {t["function"]["name"] for t in result.tool_defs}
            # Substrate stays visible; specialized tools are gone (deferred).
            assert {"web_search", "read_file", "terminal"}.issubset(names)
            assert names & set(specialized) == set()
            assert BRIDGE_TOOL_NAMES.issubset(names)
        finally:
            for n in registered:
                try:
                    registry.deregister(n)
                except Exception:
                    pass
