"""Tests for CursorStyleEngine (core compression logic).

This is the most critical module — it implements ContextEngine ABC and
orchestrates token counting, summarization, and history file management.

Key design principle tested here:
  - ONLY system prompt is protected (never initial conversation)
  - All user/assistant messages are compressed equally
  - No topic drift from anchoring initial messages
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Stub agent.auxiliary_client for unit tests
# ---------------------------------------------------------------------------

_mock_auxiliary = ModuleType("agent.auxiliary_client")
_mock_call_llm = MagicMock()
_mock_auxiliary.call_llm = _mock_call_llm


@pytest.fixture(autouse=True)
def _inject_auxiliary_stub():
    if "agent" not in sys.modules:
        sys.modules["agent"] = ModuleType("agent")
    sys.modules["agent.auxiliary_client"] = _mock_auxiliary
    _mock_call_llm.reset_mock()
    _mock_call_llm.side_effect = None
    yield
    sys.modules.pop("agent.auxiliary_client", None)


class TestCursorStyleEngineInit:
    """Tests for engine initialization and properties."""

    def test_name_returns_cursor_style(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        assert engine.name == "cursor_style"

    def test_default_threshold_percent(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        assert engine.threshold_percent == 0.50

    def test_protect_first_n_is_one(self):
        """Only system prompt is protected (1 message), not initial conversation."""
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        assert engine.protect_first_n == 1

    def test_update_model_sets_context_length(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        engine.update_model(model="gpt-4o", context_length=128000)
        assert engine.context_length == 128000
        assert engine.threshold_tokens == 64000  # 50% of 128K

    def test_update_model_recalculates_threshold(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine(threshold_percent=0.75)
        engine.update_model(model="gpt-4o", context_length=200000)
        assert engine.threshold_tokens == 150000


class TestCursorStyleEngineShouldCompress:
    """Tests for should_compress() logic."""

    def test_returns_false_below_threshold(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        engine.context_length = 200000
        engine.threshold_tokens = 100000
        engine.last_prompt_tokens = 50000
        assert engine.should_compress() is False

    def test_returns_true_at_threshold(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        engine.context_length = 200000
        engine.threshold_tokens = 100000
        engine.last_prompt_tokens = 100000
        assert engine.should_compress() is True

    def test_returns_true_above_threshold(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        engine.context_length = 200000
        engine.threshold_tokens = 100000
        engine.last_prompt_tokens = 120000
        assert engine.should_compress() is True

    def test_uses_explicit_prompt_tokens(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        engine.context_length = 200000
        engine.threshold_tokens = 100000
        assert engine.should_compress(prompt_tokens=100000) is True
        assert engine.should_compress(prompt_tokens=50000) is False

    def test_returns_false_when_cooldown_active(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        engine.context_length = 200000
        engine.threshold_tokens = 100000
        engine._compress_cooldown_until = float("inf")
        assert engine.should_compress(prompt_tokens=200000) is False


class TestCursorStyleEngineCompress:
    """Tests for compress() — the core algorithm.

    CRITICAL: These tests verify the key fix over Johor' built-in compressor:
      - Initial conversation is NOT preserved
      - Only system prompt is protected
      - Summary replaces all conversation history
    """

    def _make_engine(self, **kwargs):
        from cursor_style.engine import CursorStyleEngine
        defaults = {
            "model": "gpt-4o-mini",
            "provider": "openai",
            "api_key": "test-key",
        }
        defaults.update(kwargs)
        return CursorStyleEngine(**defaults)

    def _mock_summary_response(self, text="Summary of conversation."):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = text
        _mock_call_llm.return_value = mock_resp

    def test_compress_returns_list(self, tmp_path):
        engine = self._make_engine()
        engine.update_model(model="test", context_length=200000)
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello " * 1000},
            {"role": "assistant", "content": "Hi " * 1000},
        ]
        self._mock_summary_response()
        result = engine.compress(messages)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_system_prompt_preserved(self, tmp_path):
        """System prompt must always be preserved."""
        engine = self._make_engine()
        engine.update_model(model="test", context_length=200000)
        messages = [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": "x" * 5000},
            {"role": "assistant", "content": "y" * 5000},
        ]
        self._mock_summary_response()
        result = engine.compress(messages)
        assert result[0]["role"] == "system"
        assert "coding assistant" in result[0]["content"]

    def test_initial_user_message_NOT_preserved(self, tmp_path):
        """CRITICAL FIX: Initial user message should NOT be preserved verbatim.

        This is the key difference from Johor' ContextCompressor which
        keeps protect_first_n=3 messages (system + initial exchange).
        """
        engine = self._make_engine()
        engine.update_model(model="test", context_length=200000)
        initial_user_msg = "Help me refactor the authentication module completely."
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": initial_user_msg},
            {"role": "assistant", "content": "z" * 5000},
            {"role": "user", "content": "w" * 5000},
            {"role": "assistant", "content": "v" * 5000},
        ]
        self._mock_summary_response()
        result = engine.compress(messages)

        # The initial user message should NOT appear verbatim after system
        non_system = [m for m in result if m["role"] != "system"]
        for msg in non_system:
            assert initial_user_msg not in msg.get("content", "")

    def test_compress_reduces_message_count(self, tmp_path):
        engine = self._make_engine()
        engine.update_model(model="test", context_length=200000)
        messages = [{"role": "system", "content": "sys"}]
        for i in range(30):
            messages.append({"role": "user", "content": f"user msg {i} " * 500})
            messages.append({"role": "assistant", "content": f"assistant msg {i} " * 500})
        self._mock_summary_response()
        result = engine.compress(messages)
        assert len(result) < len(messages)

    def test_compress_includes_summary(self, tmp_path):
        engine = self._make_engine()
        engine.update_model(model="test", context_length=200000)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "x" * 5000},
            {"role": "assistant", "content": "y" * 5000},
            {"role": "user", "content": "z" * 5000},
            {"role": "assistant", "content": "w" * 5000},
        ]
        self._mock_summary_response("This is the summary.")
        result = engine.compress(messages)
        all_content = " ".join(m.get("content", "") for m in result)
        assert "This is the summary." in all_content

    def test_compress_returns_original_when_too_short(self, tmp_path):
        """Should not compress if there aren't enough messages."""
        engine = self._make_engine()
        engine.update_model(model="test", context_length=200000)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "short"},
        ]
        result = engine.compress(messages)
        assert result == messages

    def test_compress_preserves_tail_messages(self, tmp_path):
        """Recent messages should be preserved verbatim."""
        engine = self._make_engine()
        engine.update_model(model="test", context_length=200000)
        recent_msg = "This is the most recent user message."
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old " * 2000},
            {"role": "assistant", "content": "old " * 2000},
            {"role": "user", "content": recent_msg},
        ]
        self._mock_summary_response()
        result = engine.compress(messages)
        # The most recent user message should be preserved
        all_content = " ".join(m.get("content", "") for m in result)
        assert recent_msg in all_content

    def test_compress_with_focus_topic(self, tmp_path):
        engine = self._make_engine()
        engine.update_model(model="test", context_length=200000)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "x" * 5000},
            {"role": "assistant", "content": "y" * 5000},
        ]
        self._mock_summary_response()
        result = engine.compress(messages, focus_topic="auth refactor")
        # Should not crash and should produce valid output
        assert isinstance(result, list)
        assert len(result) > 0

    def test_compress_fallback_when_summary_fails(self, tmp_path):
        """When summarization fails, should still return valid messages."""
        engine = self._make_engine()
        engine.update_model(model="test", context_length=200000)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "x" * 5000},
            {"role": "assistant", "content": "y" * 5000},
            {"role": "user", "content": "z" * 5000},
            {"role": "assistant", "content": "w" * 5000},
        ]
        _mock_call_llm.side_effect = Exception("API down")
        result = engine.compress(messages)
        assert isinstance(result, list)
        assert len(result) > 0
        # System prompt should still be there
        assert result[0]["role"] == "system"


class TestCursorStyleEngineLifecycle:
    """Tests for session lifecycle methods."""

    def test_on_session_reset_clears_state(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        engine.compression_count = 5
        engine.last_prompt_tokens = 10000
        engine.on_session_reset()
        assert engine.compression_count == 0
        assert engine.last_prompt_tokens == 0

    def test_update_from_response(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        engine.update_from_response({
            "prompt_tokens": 5000,
            "completion_tokens": 1000,
            "total_tokens": 6000,
        })
        assert engine.last_prompt_tokens == 5000
        assert engine.last_completion_tokens == 1000
        assert engine.last_total_tokens == 6000

    def test_get_status_returns_expected_keys(self):
        from cursor_style.engine import CursorStyleEngine
        engine = CursorStyleEngine()
        engine.context_length = 200000
        engine.threshold_tokens = 100000
        engine.last_prompt_tokens = 50000
        status = engine.get_status()
        assert "usage_percent" in status
        assert "compression_count" in status
        assert status["usage_percent"] == 25.0
