"""
Regression tests for the 2026-08-30 JOHOR incident.

Incident: after 4 network-error retries, the attempt-5 reconnect wedged
forever inside an unprotected await (updater.stop() / pool drain /
start_polling) on a CLOSE-WAIT-poisoned httpx pool. The process stayed
alive, systemd saw a healthy service, and polling was dead for 85
minutes until a human restarted it.

Fix: every await in the reconnect ladder is armoured with
asyncio.wait_for(). These tests simulate the wedged state with awaits
that hang forever and verify the ladder keeps advancing.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    telegram_mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    telegram_mod.constants.ChatType.GROUP = "group"
    telegram_mod.constants.ChatType.SUPERGROUP = "supergroup"
    telegram_mod.constants.ChatType.CHANNEL = "channel"
    telegram_mod.constants.ChatType.PRIVATE = "private"

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, telegram_mod)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


@pytest.fixture(autouse=True)
def _no_auto_discovery(monkeypatch):
    async def _noop():
        return []
    monkeypatch.setattr("gateway.platforms.telegram.discover_fallback_ips", _noop)


@pytest.fixture(autouse=True)
def _fast_timeouts(monkeypatch):
    """Shrink armour timeouts so tests run in milliseconds, not minutes."""
    import gateway.platforms.telegram as tg_mod
    monkeypatch.setattr(tg_mod, "TELEGRAM_STOP_TIMEOUT", 0.05)
    monkeypatch.setattr(tg_mod, "TELEGRAM_DRAIN_TIMEOUT", 0.05)
    monkeypatch.setattr(tg_mod, "TELEGRAM_START_POLLING_TIMEOUT", 0.05)
    monkeypatch.setattr(tg_mod, "TELEGRAM_CONFLICT_STOP_TIMEOUT", 0.05)
    monkeypatch.setattr(tg_mod, "TELEGRAM_CONFLICT_RESTART_TIMEOUT", 0.05)


async def _hang_forever(*args, **kwargs):
    # An async side_effect is awaited by AsyncMock, so the call really
    # hangs. Event().wait() — unlike asyncio.sleep(3600) — is immune to
    # the global `patch("asyncio.sleep")` used to fast-forward the
    # ladder's back-off delays.
    await asyncio.Event().wait()


def _make_adapter() -> TelegramAdapter:
    return TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))


def _make_wedged_app(stop_hangs: bool = True, start_hangs: bool = True):
    """App mock where the pooling awaits never return (poisoned pool)."""
    mock_polling_req = AsyncMock()
    mock_polling_req.shutdown = AsyncMock(side_effect=_hang_forever)
    mock_polling_req.initialize = AsyncMock(side_effect=_hang_forever)

    mock_bot = MagicMock()
    mock_bot._request = (mock_polling_req, MagicMock())

    mock_updater = MagicMock()
    mock_updater.running = True
    mock_updater.stop = AsyncMock(side_effect=_hang_forever) if stop_hangs else AsyncMock()
    mock_updater.start_polling = AsyncMock(side_effect=_hang_forever) if start_hangs else AsyncMock()

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    mock_app.bot = mock_bot
    return mock_app, mock_polling_req


@pytest.mark.asyncio
async def test_ladder_advances_when_every_step_hangs():
    """THE 2026-08-30 incident scenario: every await in the ladder hangs.

    With wait_for armour, attempt N must still complete (each step timing
    out) instead of wedging forever, so the ladder advances toward
    fatal → exit 75 → systemd restart.
    """
    adapter = _make_adapter()
    adapter._polling_network_error_count = 1

    mock_app, _ = _make_wedged_app()
    adapter._app = mock_app

    with patch("asyncio.sleep", new_callable=AsyncMock):
        # Before the fix this call never returned (test would hang).
        await asyncio.wait_for(
            adapter._handle_polling_network_error(Exception("Timed out")),
            timeout=10,
        )

    # The failed start_polling (timed out) must self-schedule the next attempt.
    pending = [t for t in adapter._background_tasks if not t.done()]
    assert len(pending) >= 1, "ladder must self-schedule the next retry"

    for t in pending:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_start_polling_timeout_treated_as_failure_and_rescheduled():
    """start_polling hanging (not raising) must count as failure and
    schedule the next ladder attempt."""
    adapter = _make_adapter()
    adapter._polling_network_error_count = 1

    mock_app, _ = _make_wedged_app(stop_hangs=False, start_hangs=True)
    adapter._app = mock_app

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(Exception("Bad Gateway"))

    pending = [t for t in adapter._background_tasks if not t.done()]
    assert len(pending) >= 1, "timed-out start_polling must self-reschedule"

    for t in pending:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_wedged_pool_drain_does_not_block_ladder():
    """shutdown/initialize hanging in _drain_polling_connections must not
    block the ladder — wait_for caps each at the drain timeout."""
    adapter = _make_adapter()

    mock_app, mock_polling_req = _make_wedged_app(stop_hangs=False, start_hangs=False)
    adapter._app = mock_app

    await asyncio.wait_for(
        adapter._drain_polling_connections(), timeout=10
    )

    mock_polling_req.shutdown.assert_called_once()
    mock_polling_req.initialize.assert_called_once()


@pytest.mark.asyncio
async def test_conflict_retry_survives_wedged_pool():
    """Conflict path: stop/start_polling hanging must time out and return
    (awaiting the next conflict error to re-enter), not wedge forever."""
    adapter = _make_adapter()
    adapter._polling_conflict_count = 0

    mock_app, _ = _make_wedged_app(stop_hangs=True, start_hangs=True)
    adapter._app = mock_app

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await asyncio.wait_for(
            adapter._handle_polling_conflict(
                Exception("Conflict: terminated by other getUpdates")
            ),
            timeout=10,
        )

    assert adapter._polling_conflict_count == 1


@pytest.mark.asyncio
async def test_ladder_reaches_fatal_after_max_retries_with_wedged_pool():
    """End-to-end: with every step wedged, 10 attempts must still reach
    retryable fatal (→ exit 75 → systemd restart), never loop silently."""
    adapter = _make_adapter()
    adapter._polling_network_error_count = 10

    fatal_handler = AsyncMock()
    adapter.set_fatal_error_handler(fatal_handler)

    mock_app, _ = _make_wedged_app()
    adapter._app = mock_app

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await asyncio.wait_for(
            adapter._handle_polling_network_error(Exception("Timed out")),
            timeout=10,
        )

    assert adapter.has_fatal_error
    assert adapter.fatal_error_code == "telegram_network_error"
    assert adapter.fatal_error_retryable is True
    fatal_handler.assert_called_once()
