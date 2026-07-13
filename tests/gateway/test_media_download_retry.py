"""
Tests for media download retry logic added in PR #2982.

Covers:
- gateway/platforms/base.py:       cache_image_from_url
- gateway/platforms/slack.py:      SlackAdapter._download_slack_file
                                    SlackAdapter._download_slack_file_bytes
- gateway/platforms/mattermost.py: MattermostAdapter._send_url_as_file

All async tests use asyncio.run() directly — pytest-asyncio is not installed
in this environment.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

# ---------------------------------------------------------------------------
# Helpers for building httpx exceptions
# ---------------------------------------------------------------------------

def _make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://example.com/img.jpg")
    response = httpx.Response(status_code=status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response
    )


def _make_timeout_error() -> httpx.TimeoutException:
    return httpx.TimeoutException("timed out")


def _make_stream_response(content: bytes = b"\xff\xd8\xff fake media"):
    """Build a mock httpx response suitable for ``client.stream()`` usage.

    Exposes ``raise_for_status``, an empty ``headers`` mapping (no
    Content-Length), and an ``aiter_bytes`` async iterator yielding the body
    in one chunk — matching how ``_read_httpx_body_with_limit`` consumes it.
    """
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.headers = {}

    async def _aiter():
        yield content

    resp.aiter_bytes = lambda: _aiter()
    return resp


def _make_stream_client(*, responses=None, side_effect=None):
    """Build a mock httpx client whose ``.stream()`` is an async CM.

    ``responses`` is a list of response objects (or exceptions) returned on
    successive ``.stream()`` calls; ``side_effect`` is a single exception
    raised on every call. The returned client also supports being used as an
    ``async with`` context manager (``httpx.AsyncClient(...)``).
    """
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    call_state = {"i": 0}

    def _stream(method, url, **kwargs):
        idx = call_state["i"]
        call_state["i"] += 1
        if side_effect is not None:
            raise side_effect
        item = responses[idx]
        if isinstance(item, Exception):
            raise item
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=item)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    mock_client.stream = MagicMock(side_effect=_stream)
    mock_client._call_state = call_state
    return mock_client


# ---------------------------------------------------------------------------
# cache_image_from_bytes (base.py)
# ---------------------------------------------------------------------------


class TestCacheImageFromBytes:
    """Tests for gateway.platforms.base.cache_image_from_bytes"""

    def test_caches_valid_jpeg(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        from gateway.platforms.base import cache_image_from_bytes
        path = cache_image_from_bytes(b"\xff\xd8\xff fake jpeg data", ".jpg")
        assert path.endswith(".jpg")

    def test_caches_valid_png(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        from gateway.platforms.base import cache_image_from_bytes
        path = cache_image_from_bytes(b"\x89PNG\r\n\x1a\n fake png data", ".png")
        assert path.endswith(".png")

    def test_rejects_html_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        from gateway.platforms.base import cache_image_from_bytes
        with pytest.raises(ValueError, match="non-image data"):
            cache_image_from_bytes(b"<!DOCTYPE html><html><title>Slack</title></html>", ".png")

    def test_rejects_empty_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        from gateway.platforms.base import cache_image_from_bytes
        with pytest.raises(ValueError, match="non-image data"):
            cache_image_from_bytes(b"", ".jpg")

    def test_rejects_plain_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        from gateway.platforms.base import cache_image_from_bytes
        with pytest.raises(ValueError, match="non-image data"):
            cache_image_from_bytes(b"just some text, not an image", ".jpg")


# ---------------------------------------------------------------------------
# cache_image_from_url (base.py)
# ---------------------------------------------------------------------------

@patch("tools.url_safety.is_safe_url", return_value=True)
class TestCacheImageFromUrl:
    """Tests for gateway.platforms.base.cache_image_from_url"""

    def test_success_on_first_attempt(self, _mock_safe, tmp_path, monkeypatch):
        """A clean 200 response caches the image and returns a path."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        mock_client = _make_stream_client(
            responses=[_make_stream_response(b"\xff\xd8\xff fake jpeg")]
        )

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client):
                from gateway.platforms.base import cache_image_from_url
                return await cache_image_from_url(
                    "http://example.com/img.jpg", ext=".jpg"
                )

        path = asyncio.run(run())
        assert path.endswith(".jpg")
        mock_client.stream.assert_called_once()

    def test_retries_on_timeout_then_succeeds(self, _mock_safe, tmp_path, monkeypatch):
        """A timeout on the first attempt is retried; second attempt succeeds."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        mock_client = _make_stream_client(
            responses=[_make_timeout_error(), _make_stream_response(b"\xff\xd8\xff image data")]
        )
        mock_sleep = AsyncMock()

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", mock_sleep):
                from gateway.platforms.base import cache_image_from_url
                return await cache_image_from_url(
                    "http://example.com/img.jpg", ext=".jpg", retries=2
                )

        path = asyncio.run(run())
        assert path.endswith(".jpg")
        assert mock_client.stream.call_count == 2
        mock_sleep.assert_called_once()

    def test_retries_on_429_then_succeeds(self, _mock_safe, tmp_path, monkeypatch):
        """A 429 response on the first attempt is retried; second attempt succeeds."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        mock_client = _make_stream_client(
            responses=[_make_http_status_error(429), _make_stream_response(b"\xff\xd8\xff image data")]
        )

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                from gateway.platforms.base import cache_image_from_url
                return await cache_image_from_url(
                    "http://example.com/img.jpg", ext=".jpg", retries=2
                )

        path = asyncio.run(run())
        assert path.endswith(".jpg")
        assert mock_client.stream.call_count == 2

    def test_raises_after_max_retries_exhausted(self, _mock_safe, tmp_path, monkeypatch):
        """Timeout on every attempt raises after all retries are consumed."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        mock_client = _make_stream_client(side_effect=_make_timeout_error())

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                from gateway.platforms.base import cache_image_from_url
                await cache_image_from_url(
                    "http://example.com/img.jpg", ext=".jpg", retries=2
                )

        with pytest.raises(httpx.TimeoutException):
            asyncio.run(run())

        # 3 total calls: initial + 2 retries
        assert mock_client.stream.call_count == 3

    def test_non_retryable_4xx_raises_immediately(self, _mock_safe, tmp_path, monkeypatch):
        """A 404 (non-retryable) is raised immediately without any retry."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        mock_sleep = AsyncMock()
        mock_client = _make_stream_client(side_effect=_make_http_status_error(404))

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", mock_sleep):
                from gateway.platforms.base import cache_image_from_url
                await cache_image_from_url(
                    "http://example.com/img.jpg", ext=".jpg", retries=2
                )

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(run())

        # Only 1 attempt, no sleep
        assert mock_client.stream.call_count == 1
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# cache_audio_from_url (base.py)
# ---------------------------------------------------------------------------

@patch("tools.url_safety.is_safe_url", return_value=True)
class TestCacheAudioFromUrl:
    """Tests for gateway.platforms.base.cache_audio_from_url"""

    def test_success_on_first_attempt(self, _mock_safe, tmp_path, monkeypatch):
        """A clean 200 response caches the audio and returns a path."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        mock_client = _make_stream_client(
            responses=[_make_stream_response(b"\x00\x01 fake audio")]
        )

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client):
                from gateway.platforms.base import cache_audio_from_url
                return await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg"
                )

        path = asyncio.run(run())
        assert path.endswith(".ogg")
        mock_client.stream.assert_called_once()

    def test_retries_on_timeout_then_succeeds(self, _mock_safe, tmp_path, monkeypatch):
        """A timeout on the first attempt is retried; second attempt succeeds."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        mock_client = _make_stream_client(
            responses=[_make_timeout_error(), _make_stream_response(b"audio data")]
        )
        mock_sleep = AsyncMock()

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", mock_sleep):
                from gateway.platforms.base import cache_audio_from_url
                return await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg", retries=2
                )

        path = asyncio.run(run())
        assert path.endswith(".ogg")
        assert mock_client.stream.call_count == 2
        mock_sleep.assert_called_once()

    def test_retries_on_429_then_succeeds(self, _mock_safe, tmp_path, monkeypatch):
        """A 429 response on the first attempt is retried; second attempt succeeds."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        mock_client = _make_stream_client(
            responses=[_make_http_status_error(429), _make_stream_response(b"audio data")]
        )

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                from gateway.platforms.base import cache_audio_from_url
                return await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg", retries=2
                )

        path = asyncio.run(run())
        assert path.endswith(".ogg")
        assert mock_client.stream.call_count == 2

    def test_retries_on_500_then_succeeds(self, _mock_safe, tmp_path, monkeypatch):
        """A 500 response on the first attempt is retried; second attempt succeeds."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        mock_client = _make_stream_client(
            responses=[_make_http_status_error(500), _make_stream_response(b"audio data")]
        )

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                from gateway.platforms.base import cache_audio_from_url
                return await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg", retries=2
                )

        path = asyncio.run(run())
        assert path.endswith(".ogg")
        assert mock_client.stream.call_count == 2

    def test_raises_after_max_retries_exhausted(self, _mock_safe, tmp_path, monkeypatch):
        """Timeout on every attempt raises after all retries are consumed."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        mock_client = _make_stream_client(side_effect=_make_timeout_error())

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                from gateway.platforms.base import cache_audio_from_url
                await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg", retries=2
                )

        with pytest.raises(httpx.TimeoutException):
            asyncio.run(run())

        # 3 total calls: initial + 2 retries
        assert mock_client.stream.call_count == 3

    def test_non_retryable_4xx_raises_immediately(self, _mock_safe, tmp_path, monkeypatch):
        """A 404 (non-retryable) is raised immediately without any retry."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        mock_sleep = AsyncMock()
        mock_client = _make_stream_client(side_effect=_make_http_status_error(404))

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", mock_sleep):
                from gateway.platforms.base import cache_audio_from_url
                await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg", retries=2
                )

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(run())

        # Only 1 attempt, no sleep
        assert mock_client.stream.call_count == 1
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# SSRF redirect guard tests (base.py)
# ---------------------------------------------------------------------------


class TestSSRFRedirectGuard:
    """cache_image_from_url / cache_audio_from_url must reject redirects
    that land on private/internal hosts (e.g. cloud metadata endpoint)."""

    def _make_redirect_response(self, target_url: str):
        """Build a mock httpx response that looks like a redirect."""
        resp = MagicMock()
        resp.is_redirect = True
        resp.next_request = MagicMock(url=target_url)
        return resp

    def _make_client_capturing_hooks(self):
        """Return (mock_client, captured_kwargs dict) where captured_kwargs
        will contain the kwargs passed to httpx.AsyncClient()."""
        captured = {}
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        def factory(*args, **kwargs):
            captured.update(kwargs)
            return mock_client

        return mock_client, captured, factory

    def test_image_blocks_private_redirect(self, tmp_path, monkeypatch):
        """cache_image_from_url rejects a redirect to a private IP."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        redirect_resp = self._make_redirect_response(
            "http://169.254.169.254/latest/meta-data"
        )
        mock_client, captured, factory = self._make_client_capturing_hooks()

        def fake_stream(method, _url, **kwargs):
            async def _aenter(*a):
                # Simulate httpx invoking the response event hooks on the stream.
                for hook in captured["event_hooks"]["response"]:
                    await hook(redirect_resp)
                return redirect_resp
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(side_effect=_aenter)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_client.stream = MagicMock(side_effect=fake_stream)

        def fake_safe(url):
            return url == "https://public.example.com/image.png"

        async def run():
            with patch("tools.url_safety.is_safe_url", side_effect=fake_safe), \
                 patch("httpx.AsyncClient", side_effect=factory):
                from gateway.platforms.base import cache_image_from_url
                await cache_image_from_url(
                    "https://public.example.com/image.png", ext=".png"
                )

        with pytest.raises(ValueError, match="Blocked redirect"):
            asyncio.run(run())

    def test_audio_blocks_private_redirect(self, tmp_path, monkeypatch):
        """cache_audio_from_url rejects a redirect to a private IP."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        redirect_resp = self._make_redirect_response(
            "http://10.0.0.1/internal/secrets"
        )
        mock_client, captured, factory = self._make_client_capturing_hooks()

        def fake_stream(method, _url, **kwargs):
            async def _aenter(*a):
                for hook in captured["event_hooks"]["response"]:
                    await hook(redirect_resp)
                return redirect_resp
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(side_effect=_aenter)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_client.stream = MagicMock(side_effect=fake_stream)

        def fake_safe(url):
            return url == "https://public.example.com/voice.ogg"

        async def run():
            with patch("tools.url_safety.is_safe_url", side_effect=fake_safe), \
                 patch("httpx.AsyncClient", side_effect=factory):
                from gateway.platforms.base import cache_audio_from_url
                await cache_audio_from_url(
                    "https://public.example.com/voice.ogg", ext=".ogg"
                )

        with pytest.raises(ValueError, match="Blocked redirect"):
            asyncio.run(run())

    def test_safe_redirect_allowed(self, tmp_path, monkeypatch):
        """A redirect to a public IP is allowed through."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        redirect_resp = self._make_redirect_response(
            "https://cdn.example.com/real-image.png"
        )

        ok_response = _make_stream_response(b"\xff\xd8\xff fake jpeg")
        ok_response.is_redirect = False

        mock_client, captured, factory = self._make_client_capturing_hooks()

        async def _aenter(*a):
            # Public redirect passes the guard; body then streams normally.
            for hook in captured["event_hooks"]["response"]:
                await hook(redirect_resp)
            return ok_response

        def fake_stream(method, _url, **kwargs):
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(side_effect=_aenter)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_client.stream = MagicMock(side_effect=fake_stream)

        async def run():
            with patch("tools.url_safety.is_safe_url", return_value=True), \
                 patch("httpx.AsyncClient", side_effect=factory):
                from gateway.platforms.base import cache_image_from_url
                return await cache_image_from_url(
                    "https://public.example.com/image.png", ext=".jpg"
                )

        path = asyncio.run(run())
        assert path.endswith(".jpg")


