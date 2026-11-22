"""History file manager for two-tier context memory.

Inspired by Cursor's chat history files mechanism:
  - Before compression, the full conversation is saved to a JSONL file
  - After compression, the summary includes a reference to this file
  - The agent can grep/search the file to recover lost details
  - This creates a two-tier memory: summary (fast) + file (detailed)

File naming: <session_id>_compaction_<N>.jsonl
  - session_id: identifies the conversation
  - N: compression number (increments with each compression)
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_FILES = 3


class HistoryFileManager:
    """Manages conversation history files for detail recovery.

    Each time compression fires, the full pre-compression conversation
    is saved to a JSONL file. The summary can then reference this file
    so the agent can search it for details that were lost in summarization.
    """

    def __init__(self, base_dir: str, max_files: int = _DEFAULT_MAX_FILES):
        """Initialize the history file manager.

        Args:
            base_dir: Directory to store history files.
            max_files: Maximum history files to keep per session.
        """
        self.base_dir = Path(base_dir)
        self.max_files = max_files
        self._compression_counters: Dict[str, int] = {}

    def save(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
    ) -> str:
        """Save messages to a JSONL history file.

        Args:
            messages: The full conversation message list to save.
            session_id: Session identifier for filename.

        Returns:
            Absolute path to the created file.
        """
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # Increment compression counter for this session
        counter = self._compression_counters.get(session_id, 0) + 1
        self._compression_counters[session_id] = counter

        filename = f"{session_id}_compaction_{counter}.jsonl"
        filepath = self.base_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        logger.debug(
            "Saved history file: %s (%d messages)",
            filepath, len(messages),
        )

        # Cleanup old files for this session
        self._cleanup_session(session_id)

        return str(filepath.resolve())

    def get_latest(
        self, session_id: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Load the most recent history file for a session.

        Args:
            session_id: Session identifier.

        Returns:
            List of messages from the latest history file, or None.
        """
        files = self._list_session_files(session_id)
        if not files:
            return None

        latest = files[-1]  # sorted by name, last is most recent
        return self._load_file(latest)

    def get_reference_text(self, session_id: str) -> Optional[str]:
        """Generate a reference string for the latest history file.

        This text can be appended to the compression summary to tell
        the agent where to find the full conversation history.

        Args:
            session_id: Session identifier.

        Returns:
            Reference text string, or None if no history files exist.
        """
        files = self._list_session_files(session_id)
        if not files:
            return None

        latest = files[-1]
        return (
            f"[Full conversation history saved to: {latest}. "
            f"If you need details from earlier turns, read this file.]"
        )

    def cleanup_all(self) -> int:
        """Remove excess history files across all sessions.

        Returns:
            Number of files removed.
        """
        removed = 0
        if not self.base_dir.exists():
            return 0

        # Group files by session_id
        sessions: Dict[str, List[Path]] = {}
        for f in self.base_dir.glob("*.jsonl"):
            # Extract session_id from filename: <session_id>_compaction_<N>.jsonl
            name = f.stem
            parts = name.rsplit("_compaction_", 1)
            if len(parts) == 2:
                sid = parts[0]
                sessions.setdefault(sid, []).append(f)

        for sid, files in sessions.items():
            if len(files) > self.max_files:
                # Sort by name (which includes the counter)
                files.sort()
                to_remove = files[:-self.max_files]
                for f in to_remove:
                    f.unlink(missing_ok=True)
                    removed += 1
                    logger.debug("Removed old history file: %s", f)

        return removed

    def _list_session_files(self, session_id: str) -> List[Path]:
        """List history files for a session, sorted by compression number."""
        if not self.base_dir.exists():
            return []

        pattern = f"{session_id}_compaction_*.jsonl"
        files = sorted(self.base_dir.glob(pattern))
        return files

    def _cleanup_session(self, session_id: str) -> None:
        """Remove excess files for a single session."""
        files = self._list_session_files(session_id)
        if len(files) > self.max_files:
            to_remove = files[:-self.max_files]
            for f in to_remove:
                f.unlink(missing_ok=True)
                logger.debug("Removed old history file: %s", f)

    def _load_file(self, filepath: Path) -> List[Dict[str, Any]]:
        """Load messages from a JSONL file."""
        messages = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed line in %s", filepath)
        return messages
