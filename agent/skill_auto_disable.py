"""Auto-disable skills based on usage tracking."""

import logging
from datetime import datetime, timedelta
from typing import Any

from agent.skill_frontmatter import update_skill_enabled_status
from agent.skill_invocation_tracker import get_unused_skills

logger = logging.getLogger(__name__)


def auto_disable_unused_skills(config: Any, threshold_days: int = 30) -> list[str]:
    """Disable skills unused for more than threshold days.

    Updates only the skill's frontmatter (enabled: false), never modifies config.yaml.

    Args:
        config: AgentConfig instance (read-only)
        threshold_days: Days of inactivity before auto-disable

    Returns:
        List of skill names that were newly disabled
    """
    # Check if auto-disable is enabled via config
    auto_disable_cfg = _get_auto_disable_config(config)
    if not auto_disable_cfg.get("enabled", False):
        logger.debug("Auto-disable feature is disabled in config")
        return []

    threshold = auto_disable_cfg.get("threshold_days", threshold_days)
    if threshold < 1:
        threshold = 30

    newly_disabled = []

    try:
        # Get list of skills that haven't been used
        unused_skills = get_unused_skills(threshold_days=threshold)

        if not unused_skills:
            logger.debug("No unused skills found to auto-disable")
            return []

        # Get list of installed skills with their paths
        from tools.skills_tool import get_bundled_skill_path

        for skill_name in unused_skills:
            skill_path = get_bundled_skill_path(skill_name)
            if skill_path is None or not skill_path.is_dir():
                continue

            # Check if already disabled
            from agent.skill_frontmatter import get_skill_enabled_status
            current_status = get_skill_enabled_status(skill_path)
            if current_status is False:
                # Already disabled, skip
                continue

            # Update skill frontmatter: enabled: false
            if update_skill_enabled_status(skill_path, False):
                newly_disabled.append(skill_name)
                logger.info(f"Auto-disabled skill (unused {threshold}+ days): {skill_name}")
            else:
                logger.warning(f"Failed to auto-disable skill: {skill_name}")

    except Exception as e:
        logger.error(f"Error during auto-disable check: {e}")

    return newly_disabled


def should_run_auto_disable_check(config: Any) -> bool:
    """Check if it's time to run auto-disable check (once per day).

    Args:
        config: AgentConfig instance

    Returns:
        True if check should run, False otherwise
    """
    auto_disable_cfg = _get_auto_disable_config(config)
    if not auto_disable_cfg.get("enabled", False):
        return False

    check_interval = auto_disable_cfg.get("check_interval_hours", 24)
    if check_interval < 1:
        check_interval = 24

    # Parse last check timestamp from config (read-only)
    last_check_str = auto_disable_cfg.get("last_check")
    if not last_check_str:
        return True  # Never run before, run now

    try:
        last_check = datetime.fromisoformat(last_check_str.replace("Z", "+00:00"))
        elapsed = datetime.utcnow() - last_check.replace(tzinfo=None)
        return elapsed > timedelta(hours=check_interval)
    except (ValueError, TypeError):
        return True  # Invalid timestamp, run now


def _get_auto_disable_config(config: Any) -> dict:
    """Extract auto_disable config section from agent config.

    Args:
        config: AgentConfig instance

    Returns:
        Dictionary with auto_disable settings (empty dict if not configured)
    """
    try:
        # Try to get skills config section
        skills_cfg = getattr(config, "skills", {})
        if not isinstance(skills_cfg, dict):
            skills_cfg = {}

        auto_disable_cfg = skills_cfg.get("auto_disable", {})
        if not isinstance(auto_disable_cfg, dict):
            return {}

        return auto_disable_cfg
    except Exception:
        return {}
