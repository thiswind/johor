"""Tests for history_file module.

The history file mechanism (inspired by Cursor's chat history files):
  - Before compression, write the full conversation to a temp file
  - After compression, the summary includes a reference to this file
  - The agent can grep/search the file to recover lost details
  - This creates a two-tier memory system: summary (fast) + file (detailed)
"""

import json
import os
import tempfile
from pathlib import Path

import pytest


class TestHistoryFileManager:
    """Tests for HistoryFileManager class."""

    def _make_manager(self, tmp_path, max_files=3):
        from cursor_style.history_file import HistoryFileManager
        return HistoryFileManager(base_dir=str(tmp_path), max_files=max_files)

    def test_save_creates_file(self, tmp_path):
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path)
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        path = mgr.save(messages, session_id="test-123")
        assert Path(path).exists()

    def test_save_returns_path_string(self, tmp_path):
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path)
        messages = [{"role": "user", "content": "test"}]
        path = mgr.save(messages, session_id="test-456")
        assert isinstance(path, str)

    def test_save_includes_session_id_in_filename(self, tmp_path):
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path)
        messages = [{"role": "user", "content": "test"}]
        path = mgr.save(messages, session_id="my-session")
        assert "my-session" in Path(path).stem

    def test_save_stores_valid_jsonl(self, tmp_path):
        """Each line should be a valid JSON object."""
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path)
        messages = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "Second message"},
            {"role": "user", "content": "Third message"},
        ]
        path = mgr.save(messages, session_id="jsonl-test")
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 3
        for line in lines:
            data = json.loads(line.strip())
            assert "role" in data
            assert "content" in data

    def test_save_preserves_tool_calls(self, tmp_path):
        """Tool calls should be preserved in the history file."""
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path)
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "read_file",
                              "arguments": '{"path": "test.py"}'}}
            ]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": "file content here"},
        ]
        path = mgr.save(messages, session_id="tool-test")
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert "tool_calls" in first
        assert first["tool_calls"][0]["function"]["name"] == "read_file"

    def test_save_creates_base_dir_if_missing(self, tmp_path):
        from cursor_style.history_file import HistoryFileManager
        nested = tmp_path / "a" / "b" / "c"
        mgr = HistoryFileManager(base_dir=str(nested))
        messages = [{"role": "user", "content": "test"}]
        path = mgr.save(messages, session_id="mkdir-test")
        assert Path(path).exists()

    def test_save_increments_compression_number(self, tmp_path):
        """Multiple saves for the same session should have different numbers."""
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path)
        messages = [{"role": "user", "content": "test"}]
        path1 = mgr.save(messages, session_id="incr-test")
        path2 = mgr.save(messages, session_id="incr-test")
        assert path1 != path2
        # Both should exist
        assert Path(path1).exists()
        assert Path(path2).exists()

    def test_get_latest_returns_none_if_no_files(self, tmp_path):
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path)
        assert mgr.get_latest("nonexistent") is None

    def test_get_latest_returns_most_recent(self, tmp_path):
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path)
        messages = [{"role": "user", "content": "test"}]
        path1 = mgr.save(messages, session_id="latest-test")
        path2 = mgr.save(messages, session_id="latest-test")
        latest = mgr.get_latest("latest-test")
        latest_path = mgr._list_session_files("latest-test")[-1]
        assert str(latest_path.resolve()) == path2

    def test_get_latest_returns_messages_list(self, tmp_path):
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path)
        original = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "World"},
        ]
        mgr.save(original, session_id="restore-test")
        restored = mgr.get_latest("restore-test")
        assert isinstance(restored, list)
        assert len(restored) == 2
        assert restored[0]["role"] == "user"
        assert restored[0]["content"] == "Hello"

    def test_cleanup_removes_old_files(self, tmp_path):
        """Should only keep the most recent N history files per session."""
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path, max_files=2)
        messages = [{"role": "user", "content": "test"}]
        p1 = mgr.save(messages, session_id="cleanup-test")
        p2 = mgr.save(messages, session_id="cleanup-test")
        p3 = mgr.save(messages, session_id="cleanup-test")
        # Only the latest 2 should remain
        assert not Path(p1).exists()
        assert Path(p2).exists()
        assert Path(p3).exists()

    def test_cleanup_all_removes_excess(self, tmp_path):
        """cleanup_all should clean across all sessions."""
        from cursor_style.history_file import HistoryFileManager
        # Use max_files=99 so save() doesn't auto-cleanup, then cleanup_all(1)
        mgr = HistoryFileManager(base_dir=str(tmp_path), max_files=99)
        messages = [{"role": "user", "content": "test"}]
        mgr.save(messages, session_id="sess-a")
        mgr.save(messages, session_id="sess-a")
        mgr.save(messages, session_id="sess-b")
        mgr.save(messages, session_id="sess-b")
        # Now set max_files=1 and cleanup
        mgr.max_files = 1
        removed = mgr.cleanup_all()
        assert removed == 2  # 4 files total, keep 1 per session = remove 2

    def test_get_reference_text(self, tmp_path):
        """get_reference_text should return a string mentioning the file path."""
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path)
        messages = [{"role": "user", "content": "important detail"}]
        path = mgr.save(messages, session_id="ref-test")
        ref = mgr.get_reference_text("ref-test")
        assert isinstance(ref, str)
        assert path in ref
        assert len(ref) > 0

    def test_get_reference_text_returns_none_if_no_files(self, tmp_path):
        from cursor_style.history_file import HistoryFileManager
        mgr = self._make_manager(tmp_path)
        assert mgr.get_reference_text("nonexistent") is None
