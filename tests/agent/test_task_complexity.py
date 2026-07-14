"""Tests for task-complexity classification and planning enforcement (issue #3).

Covers the three public helpers in ``agent/task_complexity.py``:
  - ``classify_task_complexity`` — heuristic simple/medium/complex scoring
  - ``should_use_planning`` — auto/always/disabled mode decision
  - ``get_planning_prompt`` — the instruction injected ahead of the user message
"""

from types import SimpleNamespace

import pytest

from agent.task_complexity import (
    classify_task_complexity,
    should_use_planning,
    get_planning_prompt,
)


def _agent(mode):
    """Minimal stand-in exposing only the attribute the helper reads."""
    return SimpleNamespace(_planning_mode=mode)


# --- classify_task_complexity -------------------------------------------------

@pytest.mark.parametrize(
    "message",
    ["", "   ", None, 123, ["not", "a", "string"]],
)
def test_empty_or_non_string_is_simple(message):
    # Guards the classifier against blank/typed-wrong input — must never raise.
    assert classify_task_complexity(message) == "simple"


@pytest.mark.parametrize(
    "message",
    [
        "Hello",
        "What is the capital of France?",
        "thanks!",
        "who wrote Hamlet",
    ],
)
def test_short_knowledge_queries_are_simple(message):
    # Bare greetings and single-fact questions carry no step/tool signal.
    assert classify_task_complexity(message) == "simple"


def test_single_multi_step_indicator_is_medium():
    # One indicator ("then") scores +1 -> medium (>=2 needs another signal;
    # "write" + "test" tool keywords add the second point).
    msg = "Write the parser then test it"
    assert classify_task_complexity(msg) == "medium"


def test_multi_step_request_triggers_planning_tier():
    # Several step indicators + tool keywords push the score into the
    # planning-eligible range (medium or complex).
    msg = (
        "First analyze the codebase, then implement a new pipeline, "
        "create tests, run them, and deploy the service"
    )
    assert classify_task_complexity(msg) in ("medium", "complex")


def test_long_message_raises_complexity():
    # A >150-word body alone scores +2; pairing it with step/tool language
    # reaches the "complex" threshold (score >= 4).
    body = " ".join(
        ["first", "then", "implement", "refactor", "optimize", "deploy",
         "create", "test", "run", "analyze"]
        + ["word"] * 150
    )
    assert classify_task_complexity(body) == "complex"


# --- should_use_planning ------------------------------------------------------

@pytest.mark.parametrize("complexity", ["complex", "medium"])
def test_auto_mode_plans_for_medium_and_complex(complexity):
    assert should_use_planning(complexity, _agent("auto")) is True


def test_auto_mode_skips_simple():
    assert should_use_planning("simple", _agent("auto")) is False


def test_no_agent_defaults_to_auto_behavior():
    # Without an agent (no config), fall back to the auto policy.
    assert should_use_planning("complex", None) is True
    assert should_use_planning("simple", None) is False


@pytest.mark.parametrize("complexity", ["simple", "medium", "complex"])
def test_disabled_mode_never_plans(complexity):
    assert should_use_planning(complexity, _agent("disabled")) is False


@pytest.mark.parametrize("complexity", ["simple", "medium", "complex"])
def test_always_mode_plans_for_everything(complexity):
    assert should_use_planning(complexity, _agent("always")) is True


def test_mode_matching_is_case_and_space_insensitive():
    # agent_init stores the raw config value; the helper normalizes it.
    assert should_use_planning("simple", _agent("  ALWAYS  ")) is True
    assert should_use_planning("complex", _agent("Disabled")) is False


# --- get_planning_prompt ------------------------------------------------------

def test_planning_prompt_is_actionable_and_nonempty():
    prompt = get_planning_prompt("build the thing")
    assert isinstance(prompt, str) and prompt.strip()
    lowered = prompt.lower()
    assert "step" in lowered
    assert "execute" in lowered
