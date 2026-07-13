"""Track skill invocations for usage-based auto-disable."""

import json
import logging
from datetime import datetime
from pathlib import Path

from hermes_constants import get_hermes_home
from utils import atomic_json_write

logger = logging.getLogger(__name__)


def track_skill_invocation(skill_name: str, platform: str = "") -> None:
    """Record a skill invocation in usage log.

    Args:
        skill_name: Name of the skill invoked
        platform: Platform where skill was invoked (optional)
    """
    if not skill_name or not skill_name.strip():
        return

    hermes_home = get_hermes_home()
    log_dir = hermes_home / "skill_usage"
    log_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d")
    log_file = log_dir / f"usage_{date_str}.jsonl"

    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "skill_name": skill_name.strip(),
        "platform": platform.strip() if platform else "",
    }

    try:
        atomic_json_write(log_file, entry, append=True, default=str)
    except Exception as e:
        logger.warning(f"Failed to record skill invocation for {skill_name}: {e}")


def get_skill_last_used(skill_name: str) -> datetime | None:
    """Get last invocation timestamp for a skill.

    Args:
        skill_name: Name of the skill

    Returns:
        Last invocation datetime (UTC), or None if skill not found
    """
    if not skill_name or not skill_name.strip():
        return None

    hermes_home = get_hermes_home()
    log_dir = hermes_home / "skill_usage"

    if not log_dir.is_dir():
        return None

    latest_timestamp = None

    try:
        for log_file in sorted(log_dir.glob("usage_*.jsonl"), reverse=True):
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            entry = json.loads(line)
                            if entry.get("skill_name") == skill_name.strip():
                                timestamp_str = entry.get("timestamp")
                                if timestamp_str:
                                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                                    if latest_timestamp is None or timestamp > latest_timestamp:
                                        latest_timestamp = timestamp
                        except (json.JSONDecodeError, ValueError):
                            continue
            except Exception as e:
                logger.debug(f"Error reading {log_file}: {e}")
                continue
    except Exception as e:
        logger.warning(f"Error reading skill usage logs: {e}")

    return latest_timestamp


def get_unused_skills(threshold_days: int = 30) -> list[str]:
    """Get list of skills not used in the last N days.

    Args:
        threshold_days: Number of days of inactivity before marking as unused

    Returns:
        List of unused skill names
    """
    from datetime import timedelta

    if threshold_days < 1:
        threshold_days = 30

    cutoff_date = datetime.utcnow() - timedelta(days=threshold_days)
    unused = []

    try:
        from tools.skills_tool import get_bundled_skill_names

        for skill_name in get_bundled_skill_names():
            last_used = get_skill_last_used(skill_name)
            if last_used is None or last_used < cutoff_date:
                unused.append(skill_name)
    except Exception as e:
        logger.warning(f"Error getting unused skills: {e}")

    return unused
