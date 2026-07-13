"""Task complexity classification and planning enforcement.

Classifies incoming user requests as simple/medium/complex and enforces
planning mode for complex tasks where decomposition improves efficiency.
"""

from typing import Any, Optional, Literal


def classify_task_complexity(
    user_message: str,
    agent: Optional[Any] = None
) -> Literal["simple", "medium", "complex"]:
    """Classify task complexity based on heuristics and optional LLM call.

    Heuristics evaluate:
    - Message length (word count)
    - Multi-step indicators (keywords like "then", "after", "steps", etc.)
    - Tool requirements implied by request

    Args:
        user_message: The user's request text
        agent: Optional agent instance (for LLM-based classification if enabled)

    Returns:
        Complexity level: "simple", "medium", or "complex"
    """
    if not user_message or not isinstance(user_message, str):
        return "simple"

    message_lower = user_message.lower().strip()
    word_count = len(message_lower.split())

    # Multi-step indicators suggesting complex task
    multi_step_indicators = [
        "first", "then", "after that", "next", "following", "subsequent",
        "steps", "process", "stages", "phases", "pipeline", "workflow",
        "implement", "build", "create", "generate", "analyze",
        "investigate", "research", "multiple", "several", "multiple times",
        "refactor", "redesign", "restructure", "optimize", "improve"
    ]

    step_indicator_count = sum(
        1 for indicator in multi_step_indicators
        if indicator in message_lower
    )

    # Complexity heuristic scoring
    complexity_score = 0

    # Word count factor
    if word_count > 150:
        complexity_score += 2
    elif word_count > 100:
        complexity_score += 1

    # Multi-step indicators
    if step_indicator_count >= 3:
        complexity_score += 2
    elif step_indicator_count >= 1:
        complexity_score += 1

    # Tool-related keywords suggesting need for execution
    tool_indicators = ["write", "create", "deploy", "test", "run", "execute", "query"]
    tool_count = sum(1 for indicator in tool_indicators if indicator in message_lower)
    if tool_count >= 2:
        complexity_score += 1

    # Classification thresholds
    if complexity_score >= 4:
        return "complex"
    elif complexity_score >= 2:
        return "medium"
    else:
        return "simple"


def should_use_planning(
    complexity: Literal["simple", "medium", "complex"],
    agent: Optional[Any] = None
) -> bool:
    """Determine if planning should be enforced for this task.

    Args:
        complexity: Task complexity classification
        agent: Optional agent instance for config check

    Returns:
        True if planning should be enforced
    """
    # Check for planning enablement in agent config
    if agent is not None:
        planning_mode = getattr(agent, "_planning_mode", "auto").lower().strip()
        if planning_mode == "disabled":
            return False
        elif planning_mode == "always":
            return True

    # "Auto" mode: use planning for complex and medium tasks
    return complexity in ("complex", "medium")


def get_planning_prompt(user_message: str) -> str:
    """Build a planning-mode system prompt injection.

    Instructs the model to break down the task into steps before execution.

    Args:
        user_message: The original user request

    Returns:
        Planning mode instruction text
    """
    return (
        "Before executing this request, break it down into clear, numbered steps. "
        "Identify dependencies and sequencing. Then execute step by step, "
        "reporting progress. Verify each step before proceeding."
    )
