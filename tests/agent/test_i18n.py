"""Tests for agent.i18n -- English-only translation shim."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent import i18n


LOCALES_DIR = Path(__file__).resolve().parents[2] / "locales"


def _load_raw(lang: str) -> dict:
    with (LOCALES_DIR / f"{lang}.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _flatten(d, prefix="") -> dict:
    flat = {}
    for k, v in (d or {}).items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten(v, key))
        else:
            flat[key] = v
    return flat


def test_english_catalog_exists():
    """English catalog must exist."""
    assert (LOCALES_DIR / "en.yaml").is_file(), "missing locales/en.yaml"


def test_only_english_supported():
    """Only English language is supported."""
    assert i18n.SUPPORTED_LANGUAGES == ("en",)
    assert i18n.DEFAULT_LANGUAGE == "en"


# ---------------------------------------------------------------------------
# t() semantics
# ---------------------------------------------------------------------------

def test_t_returns_english():
    """t() always returns English strings."""
    assert i18n.t("approval.denied").endswith("Denied")
    assert i18n.t("gateway.reset.header_default") == "✨ Session reset! Starting fresh."


def test_t_formats_placeholders():
    """t() formats placeholder arguments."""
    msg = i18n.t("gateway.draining", count=3)
    assert "3" in msg


def test_t_missing_key_returns_key():
    """A missing key returns its own path -- ugly but never crashes."""
    result = i18n.t("nonexistent.key.path")
    assert result == "nonexistent.key.path"


def test_get_language_returns_english():
    """get_language() always returns 'en'."""
    assert i18n.get_language() == "en"


# ---------------------------------------------------------------------------
# _locales_dir resolution ladder -- regression for #23943 / #27632 / #35374.
# Sealed installs (Nix store venv, pip wheel) have no source tree next to
# agent/, so _locales_dir must resolve via env override or the data scheme.
# ---------------------------------------------------------------------------

def test_locales_dir_env_override_used_when_dir_exists(tmp_path, monkeypatch):
    """HERMES_BUNDLED_LOCALES wins when it points at a real directory."""
    bundled = tmp_path / "bundled-locales"
    bundled.mkdir()
    monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(bundled))
    assert i18n._locales_dir() == bundled


def test_locales_dir_env_override_ignored_when_missing(tmp_path, monkeypatch):
    """A bogus HERMES_BUNDLED_LOCALES falls through to source/wheel resolution
    instead of returning a path that doesn't exist."""
    monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(tmp_path / "does-not-exist"))
    result = i18n._locales_dir()
    assert result != tmp_path / "does-not-exist"
    # In a source checkout this is the repo-root locales dir.
    assert result.name == "locales"


def test_locales_dir_falls_back_to_data_scheme(tmp_path, monkeypatch):
    """When neither the env override nor a source-adjacent locales/ exists,
    _locales_dir uses sysconfig's data scheme (the pip-wheel layout)."""
    import sysconfig

    # No env override.
    monkeypatch.delenv("HERMES_BUNDLED_LOCALES", raising=False)

    # Force the source-adjacent path to a location with no locales/ dir.
    fake_pkg = tmp_path / "site-packages" / "agent"
    fake_pkg.mkdir(parents=True)
    monkeypatch.setattr(i18n, "__file__", str(fake_pkg / "i18n.py"))

    # Stand up a fake data scheme containing locales/.
    data_root = tmp_path / "data-scheme"
    (data_root / "locales").mkdir(parents=True)
    real_get_path = sysconfig.get_path

    def fake_get_path(name, *args, **kwargs):
        if name == "data":
            return str(data_root)
        return real_get_path(name, *args, **kwargs)

    monkeypatch.setattr(i18n.sysconfig, "get_path", fake_get_path)

    assert i18n._locales_dir() == data_root / "locales"


def test_t_resolves_real_string_in_source_checkout():
    """Sanity: in the test environment (a source checkout) t() must return a
    human string, never the bare key path. Guards against catalog-load
    regressions independent of packaging."""
    assert i18n.t("gateway.reset.header_default", lang="en") != "gateway.reset.header_default"
    assert i18n.t("gateway.status.header", lang="en") != "gateway.status.header"
