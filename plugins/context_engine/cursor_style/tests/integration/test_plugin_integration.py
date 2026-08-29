"""Integration tests: verify plugin works with JOHOR Agent's plugin system.

These tests require JOHOR Agent to be in PYTHONPATH.
They verify:
  1. The engine is a valid ContextEngine subclass
  2. The register() function works with the plugin collector
  3. End-to-end compression produces valid output
"""

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

HERMES_PATH = Path("/data/user/work/hermes-agent")
HAS_HERMES = HERMES_PATH.exists()

pytestmark = pytest.mark.skipif(
    not HAS_HERMES,
    reason="JOHOR Agent not available for integration tests",
)

# ---------------------------------------------------------------------------
# Stub agent.auxiliary_client for E2E tests (openai not installed in sandbox)
# ---------------------------------------------------------------------------

_mock_auxiliary = ModuleType("agent.auxiliary_client")
_mock_call_llm = MagicMock()
_mock_auxiliary.call_llm = _mock_call_llm


@pytest.fixture(autouse=True)
def _inject_auxiliary_stub():
    # Only inject if agent.auxiliary_client is not already available
    try:
        import agent.auxiliary_client  # noqa: F401
    except (ImportError, ModuleNotFoundError):
        if "agent" not in sys.modules:
            sys.modules["agent"] = ModuleType("agent")
        sys.modules["agent.auxiliary_client"] = _mock_auxiliary
    _mock_call_llm.reset_mock()
    _mock_call_llm.side_effect = None
    yield
    # Only cleanup if we injected it
    if sys.modules.get("agent.auxiliary_client") is _mock_auxiliary:
        sys.modules.pop("agent.auxiliary_client", None)


class TestPluginIntegration:
    """Verify the plugin integrates with JOHOR Agent."""

    def test_engine_is_context_engine_subclass(self):
        """CursorStyleEngine must be a valid ContextEngine."""
        sys.path.insert(0, str(HERMES_PATH))
        try:
            from agent.context_engine import ContextEngine
            from cursor_style.engine import CursorStyleEngine
            assert issubclass(CursorStyleEngine, ContextEngine)
        finally:
            sys.path.remove(str(HERMES_PATH))

    def test_register_function_exists(self):
        """The plugin must expose a register() function."""
        from cursor_style.engine import register
        assert callable(register)

    def test_register_creates_engine(self):
        """register() should create and register a CursorStyleEngine."""
        from cursor_style.engine import register, CursorStyleEngine

        class FakeCollector:
            def __init__(self):
                self.engine = None

            def register_context_engine(self, engine):
                self.engine = engine

        collector = FakeCollector()
        register(collector)
        assert isinstance(collector.engine, CursorStyleEngine)
        assert collector.engine.name == "cursor_style"

    def test_engine_has_all_required_methods(self):
        """Engine must implement all abstract methods."""
        sys.path.insert(0, str(HERMES_PATH))
        try:
            from agent.context_engine import ContextEngine
            from cursor_style.engine import CursorStyleEngine

            engine = CursorStyleEngine()

            # Check all abstract methods are implemented
            assert hasattr(engine, "update_from_response")
            assert hasattr(engine, "should_compress")
            assert hasattr(engine, "compress")
            assert callable(engine.update_from_response)
            assert callable(engine.should_compress)
            assert callable(engine.compress)

            # Check class-level attributes
            assert hasattr(engine, "last_prompt_tokens")
            assert hasattr(engine, "threshold_tokens")
            assert hasattr(engine, "context_length")
            assert hasattr(engine, "compression_count")
        finally:
            sys.path.remove(str(HERMES_PATH))

    def test_full_lifecycle_with_hermes_abc(self):
        """Full lifecycle: init -> session_start -> update -> compress -> reset."""
        sys.path.insert(0, str(HERMES_PATH))
        try:
            from agent.context_engine import ContextEngine
            from cursor_style.engine import CursorStyleEngine

            engine = CursorStyleEngine(
                model="test-model",
                provider="test-provider",
                api_key="test-key",
            )

            # Model setup
            engine.update_model(
                model="test-model",
                context_length=128000,
                provider="test-provider",
                api_key="test-key",
            )
            assert engine.context_length == 128000
            assert engine.threshold_tokens == 64000

            # Session start
            engine.on_session_start("test-session-123", hermes_home="/tmp/test")

            # Token tracking
            engine.update_from_response({
                "prompt_tokens": 70000,
                "completion_tokens": 2000,
                "total_tokens": 72000,
            })
            assert engine.last_prompt_tokens == 70000

            # Should compress
            assert engine.should_compress() is True

            # Session reset
            engine.on_session_reset()
            assert engine.compression_count == 0
            assert engine.last_prompt_tokens == 0
        finally:
            sys.path.remove(str(HERMES_PATH))


class TestEndToEndCompression:
    """End-to-end compression tests with realistic message sequences."""

    def test_long_conversation_compression(self):
        """Simulate a real long conversation and verify compression works."""
        from cursor_style.engine import CursorStyleEngine

        engine = CursorStyleEngine(
            model="test", provider="test", api_key="test",
        )
        engine.update_model(model="test", context_length=200000)
        engine.on_session_start("e2e-test", hermes_home="/tmp/e2e-test")

        # Build a realistic conversation (50+ messages)
        messages = [{"role": "system", "content": "You are a coding assistant."}]
        for i in range(25):
            messages.append({
                "role": "user",
                "content": f"Step {i}: Please help with the auth module refactoring. " * 50,
            })
            messages.append({
                "role": "assistant",
                "content": f"I'll work on step {i} of the refactoring. " * 50,
            })

        # Mock the LLM call
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = (
            "The user has been working on refactoring the auth module. "
            "Steps 0-24 have been completed. Current state: JWT implementation "
            "is in progress. Key files modified: auth/login.py, auth/jwt.py."
        )
        _mock_call_llm.return_value = mock_resp

        result = engine.compress(messages)

        # Verify output
        assert isinstance(result, list)
        assert len(result) < len(messages)
        assert result[0]["role"] == "system"

        # Verify initial user message is NOT preserved verbatim
        initial_msg = "Step 0: Please help with the auth module refactoring."
        non_system = [m for m in result if m["role"] != "system"]
        for msg in non_system:
            assert initial_msg not in msg.get("content", "")

        # Verify compression count incremented
        assert engine.compression_count == 1

    def test_no_topic_drift_after_compression(self):
        """CRITICAL: After compression, the initial topic should NOT dominate."""
        from cursor_style.engine import CursorStyleEngine

        engine = CursorStyleEngine(
            model="test", provider="test", api_key="test",
        )
        engine.update_model(model="test", context_length=200000)

        initial_request = "Help me set up the project structure"
        current_request = "Now implement the payment processing module"

        messages = [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": initial_request},
            {"role": "assistant", "content": "done " * 2000},
            {"role": "user", "content": "next step " * 2000},
            {"role": "assistant", "content": "done " * 2000},
            {"role": "user", "content": "more work " * 2000},
            {"role": "assistant", "content": "done " * 2000},
            {"role": "user", "content": current_request + " " * 5000},
        ]

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = (
            "Project structure was set up earlier. Currently working on "
            "payment processing. The user just asked to implement the "
            "payment processing module."
        )
        _mock_call_llm.return_value = mock_resp

        result = engine.compress(messages)

        # The initial request should NOT appear in the output
        all_content = " ".join(m.get("content", "") for m in result)
        assert initial_request not in all_content

        # The summary should reflect the current state (not drift to initial topic)
        assert "payment processing" in all_content.lower()
