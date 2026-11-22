"""Tests for summarizer module.

Cursor-style summarizer: minimal prompt, ~1000 token output, no structured
template that could be misinterpreted as instructions.
"""

import json
import sys
from types import SimpleNamespace, ModuleType
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub agent.auxiliary_client for unit tests (no Johor dependency)
# ---------------------------------------------------------------------------

_mock_auxiliary = ModuleType("agent.auxiliary_client")
_mock_call_llm = MagicMock()
_mock_auxiliary.call_llm = _mock_call_llm


@pytest.fixture(autouse=True)
def _inject_auxiliary_stub():
    """Inject a mock agent.auxiliary_client into sys.modules."""
    # Ensure agent package exists
    if "agent" not in sys.modules:
        sys.modules["agent"] = ModuleType("agent")
    sys.modules["agent.auxiliary_client"] = _mock_auxiliary
    _mock_call_llm.reset_mock()
    _mock_call_llm.side_effect = None
    yield
    # Cleanup
    sys.modules.pop("agent.auxiliary_client", None)


class TestSummarizer:
    """Tests for Summarizer class."""

    def _make_summarizer(self, **kwargs):
        from cursor_style.summarizer import Summarizer
        defaults = {
            "model": "gpt-4o-mini",
            "provider": "openai",
            "base_url": "",
            "api_key": "test-key",
        }
        defaults.update(kwargs)
        return Summarizer(**defaults)

    def test_serialize_messages_basic(self):
        """Messages should be serialized to a readable text format."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        messages = [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        text = s.serialize_messages(messages)
        assert "user" in text.lower() or "User" in text
        assert "Hello!" in text
        assert "assistant" in text.lower() or "Assistant" in text
        assert "Hi there!" in text

    def test_serialize_messages_with_tool_calls(self):
        """Tool calls should be included in serialization."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "read_file",
                              "arguments": '{"path": "test.py"}'}}
            ]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": "def foo(): pass"},
        ]
        text = s.serialize_messages(messages)
        assert "read_file" in text
        assert "test.py" in text

    def test_serialize_truncates_long_tool_outputs(self):
        """Long tool outputs should be truncated in serialization."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        long_content = "x" * 10000
        messages = [
            {"role": "tool", "tool_call_id": "call_1", "content": long_content},
        ]
        text = s.serialize_messages(messages)
        # Should be much shorter than the original
        assert len(text) < 5000

    def test_build_first_compaction_prompt(self):
        """First compaction prompt should be minimal (Cursor-style)."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        prompt = s.build_prompt(
            turns_text="User: Hello\nAssistant: Hi!",
            summary_budget=1000,
        )
        # Should NOT contain the verbose 11-section template
        assert "## Goal" not in prompt
        assert "## Completed Actions" not in prompt
        assert "## Remaining Work" not in prompt
        # Should be relatively short (Cursor: minimal prompt)
        assert len(prompt) < 2000

    def test_build_incremental_prompt_includes_previous(self):
        """Incremental prompt should include the previous summary."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        s.previous_summary = "Previous summary content here."
        prompt = s.build_prompt(
            turns_text="New turns...",
            summary_budget=1000,
        )
        assert "Previous summary content here." in prompt

    def test_build_prompt_includes_budget(self):
        """Prompt should mention the token budget."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        prompt = s.build_prompt(
            turns_text="Some turns",
            summary_budget=1500,
        )
        assert "1500" in prompt

    def test_build_prompt_with_focus_topic(self):
        """Focus topic should be included in the prompt."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        prompt = s.build_prompt(
            turns_text="Some turns",
            summary_budget=1000,
            focus_topic="authentication refactor",
        )
        assert "authentication refactor" in prompt

    def test_summarize_calls_llm(self):
        """summarize() should call the LLM and return the result."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "World"},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Summary of conversation."
        _mock_call_llm.return_value = mock_response

        result = s.summarize(messages, summary_budget=500)

        assert result == "Summary of conversation."
        assert s.previous_summary == "Summary of conversation."

    def test_summarize_returns_none_on_failure(self):
        """summarize() should return None when LLM call fails."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        messages = [{"role": "user", "content": "Hello"}]

        _mock_call_llm.side_effect = Exception("API error")
        result = s.summarize(messages, summary_budget=500)

        assert result is None

    def test_summarize_uses_auxiliary_model(self):
        """summarize() should use the auxiliary model, not the main model."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer(model="claude-3-opus", summary_model="gpt-4o-mini")
        messages = [{"role": "user", "content": "Hello"}]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Summary."
        _mock_call_llm.return_value = mock_response

        s.summarize(messages, summary_budget=500)
        call_kwargs = _mock_call_llm.call_args[1]
        # Should use summary_model if set
        assert call_kwargs.get("model") == "gpt-4o-mini"

    def test_summarize_strips_thinking_tags(self):
        """Response should have thinking tags stripped."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        messages = [{"role": "user", "content": "Hello"}]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            "<think internal-reasoning>\nLet me analyze...\n</think internal-reasoning>\n"
            "This is the actual summary."
        )
        _mock_call_llm.return_value = mock_response
        result = s.summarize(messages, summary_budget=500)

        assert "<think" not in result
        assert "This is the actual summary." in result

    def test_compute_budget_scales_with_content(self):
        """Budget should scale with content size but have limits."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer(context_length=200000)
        # Small content
        budget_small = s.compute_budget(1000)
        assert budget_small >= 500  # minimum
        # Large content
        budget_large = s.compute_budget(50000)
        assert budget_large <= 3000  # ceiling

    def test_prune_tool_outputs(self):
        """Old tool outputs should be replaced with summaries."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "read_file",
                              "arguments": '{"path": "a.py"}'}}
            ]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": "very long file content " * 100},
            {"role": "user", "content": "Continue"},
            {"role": "assistant", "content": "Done"},
        ]
        result = s.prune_tool_outputs(messages, protect_tail_count=2)
        # The old tool output should be pruned
        assert len(result[1]["content"]) < len(messages[1]["content"])

    def test_prune_preserves_tail(self):
        """Tail messages should not be pruned."""
        from cursor_style.summarizer import Summarizer
        s = self._make_summarizer()
        original_content = "important recent output " * 100
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "read_file",
                              "arguments": '{"path": "a.py"}'}}
            ]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": "old output " * 100},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_2", "type": "function",
                 "function": {"name": "read_file",
                              "arguments": '{"path": "b.py"}'}}
            ]},
            {"role": "tool", "tool_call_id": "call_2",
             "content": original_content},
        ]
        result = s.prune_tool_outputs(messages, protect_tail_count=2)
        # Last tool output should be preserved
        assert result[3]["content"] == original_content
