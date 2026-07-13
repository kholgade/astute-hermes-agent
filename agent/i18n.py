"""Lightweight English-only translation shim.

Replaces the full i18n module with a minimal wrapper that loads
English strings from locales/en.yaml only. This maintains backward
compatibility with existing code that calls t() while eliminating
multi-language support.
"""

import logging
import os
import sysconfig
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("en",)
DEFAULT_LANGUAGE = "en"


def _locales_dir() -> Path:
    """Return the directory containing locale YAML files.

    Resolution order, first existing wins:

    1. ``HERMES_BUNDLED_LOCALES`` env var -- set by the Nix wrapper (or any
       sealed-packaging system) to point at the installed catalog directory.
    2. ``<repo-root>/locales`` -- source checkouts and ``pip install -e .``,
       where the working tree sits next to ``agent/``.
    3. ``<sysconfig data|purelib|platlib>/locales`` -- pip wheel installs.
       setuptools ``data-files`` extracts ``locales/*.yaml`` under the
       interpreter's ``data`` scheme; the other schemes are checked as a
       safety net for nonstandard layouts. Regression guard for
       #23943 / #27632 / #35374 -- without this, sealed installs (pip wheel,
       Nix store venv) drop the catalog and t() surfaces raw key paths.
    """
    env_override = os.getenv("HERMES_BUNDLED_LOCALES", "").strip()
    if env_override:
        candidate = Path(env_override)
        if candidate.is_dir():
            return candidate

    source_dir = Path(__file__).resolve().parent.parent / "locales"
    if source_dir.is_dir():
        return source_dir

    for scheme in ("data", "purelib", "platlib"):
        raw = sysconfig.get_path(scheme)
        if not raw:
            continue
        candidate = Path(raw) / "locales"
        if candidate.is_dir():
            return candidate

    return source_dir


@lru_cache(maxsize=1)
def _load_english_catalog() -> dict[str, str]:
    """Load English catalog from en.yaml."""
    path = _locales_dir() / "en.yaml"
    if not path.is_file():
        logger.warning("English locale file missing at %s", path)
        return {}

    try:
        import yaml
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("Failed to load English catalog: %s", exc)
        return {}

    flat: dict[str, str] = {}
    _flatten_into(raw, "", flat)
    return flat


def _flatten_into(node: Any, prefix: str, out: dict[str, str]) -> None:
    """Flatten nested YAML dict into dotted-key format."""
    if isinstance(node, dict):
        for key, value in node.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            _flatten_into(value, child_key, out)
    elif isinstance(node, str):
        out[prefix] = node


def t(key: str, **format_kwargs: Any) -> str:
    """Translate key to English (only language supported).

    Parameters
    ----------
    key
        Dotted path into the catalog, e.g. "approval.choose_long".
    **format_kwargs
        str.format substitution arguments.

    Returns
    -------
    The English string, or the key itself if not found.
    """
    catalog = _load_english_catalog()
    value = catalog.get(key)

    if value is None:
        logger.debug("i18n miss: key=%r", key)
        return key

    if format_kwargs:
        try:
            return value.format(**format_kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("i18n format failed for key=%r kwargs=%r: %s", key, format_kwargs, exc)
            return value

    return value


def get_language() -> str:
    """Return the active language (always English)."""
    return DEFAULT_LANGUAGE


def reset_language_cache() -> None:
    """Clear the catalog cache."""
    _load_english_catalog.cache_clear()


__all__ = [
    "SUPPORTED_LANGUAGES",
    "DEFAULT_LANGUAGE",
    "t",
    "get_language",
    "reset_language_cache",
]
