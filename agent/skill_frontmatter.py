"""Manage skill frontmatter for enabled/disabled state persistence."""

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def update_skill_enabled_status(skill_path: Path, enabled: bool) -> bool:
    """Update the 'enabled' field in skill's frontmatter.

    Args:
        skill_path: Path to skill directory (containing SKILL.md)
        enabled: Whether skill should be enabled

    Returns:
        True if successful, False otherwise
    """
    skill_md = skill_path / "SKILL.md"
    if not skill_md.is_file():
        logger.warning(f"Skill SKILL.md not found: {skill_md}")
        return False

    try:
        content = skill_md.read_text(encoding="utf-8")
        updated = _update_frontmatter_enabled(content, enabled)

        if updated != content:
            # Atomic write
            skill_md.write_text(updated, encoding="utf-8")
            logger.debug(f"Updated skill enabled status to {enabled}: {skill_path.name}")
            return True
        else:
            logger.debug(f"Skill enabled status already {enabled}: {skill_path.name}")
            return True
    except Exception as e:
        logger.error(f"Failed to update skill frontmatter {skill_path}: {e}")
        return False


def ensure_skill_frontmatter_enabled_field(skill_path: Path) -> bool:
    """Migration helper: ensure skill has 'enabled' field in frontmatter.

    If skill lacks 'enabled' field, add it as 'enabled: true'.

    Args:
        skill_path: Path to skill directory (containing SKILL.md)

    Returns:
        True if field exists or was added, False on error
    """
    skill_md = skill_path / "SKILL.md"
    if not skill_md.is_file():
        return True  # No SKILL.md, not a classic skill

    try:
        content = skill_md.read_text(encoding="utf-8")

        # Parse frontmatter
        frontmatter = _extract_frontmatter(content)
        if frontmatter is None:
            return True  # No frontmatter, skip

        # Check if field exists
        if "enabled" in frontmatter:
            return True  # Already has field

        # Add enabled: true
        updated = _update_frontmatter_enabled(content, True)
        if updated != content:
            skill_md.write_text(updated, encoding="utf-8")
            logger.debug(f"Added enabled field to {skill_path.name}")
        return True
    except Exception as e:
        logger.warning(f"Failed to ensure enabled field for {skill_path}: {e}")
        return False


def get_skill_enabled_status(skill_path: Path) -> Optional[bool]:
    """Get the 'enabled' status from skill's frontmatter.

    Args:
        skill_path: Path to skill directory (containing SKILL.md)

    Returns:
        True if enabled, False if disabled, None if field doesn't exist or error
    """
    skill_md = skill_path / "SKILL.md"
    if not skill_md.is_file():
        return None

    try:
        content = skill_md.read_text(encoding="utf-8")
        frontmatter = _extract_frontmatter(content)
        if frontmatter is None:
            return None

        enabled_val = frontmatter.get("enabled")
        if enabled_val is None:
            return None

        # Normalize boolean values
        if isinstance(enabled_val, bool):
            return enabled_val
        if isinstance(enabled_val, str):
            return enabled_val.lower() in ("true", "yes", "1")
        return None
    except Exception as e:
        logger.warning(f"Failed to read enabled status for {skill_path}: {e}")
        return None


def _extract_frontmatter(content: str) -> Optional[dict]:
    """Extract frontmatter dict from markdown content.

    Args:
        content: Full SKILL.md content

    Returns:
        Frontmatter dict, or None if no valid frontmatter found
    """
    if not content.startswith("---"):
        return None

    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not match:
        return None

    frontmatter_str = match.group(1)
    frontmatter = {}

    for line in frontmatter_str.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            frontmatter[key] = val

    return frontmatter


def _update_frontmatter_enabled(content: str, enabled: bool) -> str:
    """Update or add the 'enabled' field in frontmatter.

    Args:
        content: Full SKILL.md content
        enabled: New enabled status

    Returns:
        Updated content with 'enabled' field set
    """
    if not content.startswith("---"):
        # No frontmatter, prepend one
        enabled_val = "true" if enabled else "false"
        return f"---\nenabled: {enabled_val}\n---\n{content}"

    match = re.match(r"^(---\n)(.*?)(\n---\n)", content, re.DOTALL)
    if not match:
        return content

    prefix = match.group(1)
    frontmatter_str = match.group(2)
    suffix = match.group(3)
    rest = content[match.end():]

    # Replace or add 'enabled' line
    enabled_val = "true" if enabled else "false"
    if re.search(r"^enabled:\s*", frontmatter_str, re.MULTILINE):
        # Replace existing line
        updated_fm = re.sub(
            r"^enabled:\s*.*$",
            f"enabled: {enabled_val}",
            frontmatter_str,
            flags=re.MULTILINE
        )
    else:
        # Add new line after opening ---
        updated_fm = f"enabled: {enabled_val}\n{frontmatter_str}"

    return f"{prefix}{updated_fm}{suffix}{rest}"
