"""CursorStyleEngine — Cursor-inspired context compression engine.

This is the main entry point that implements ContextEngine ABC and
orchestrates all sub-modules.

Key design principle: ONLY the system prompt is protected. All user/assistant
messages are compressed equally. This prevents the topic drift bug in
Johor' built-in ContextCompressor where initial conversations are
permanently anchored.

Compression algorithm:
  1. Save full history to file (two-tier memory)
  2. Prune old tool outputs (cheap, no LLM)
  3. Find tail boundary by token budget
  4. Generate minimal summary (Cursor-style prompt, ~1000 tokens)
  5. Assemble: system prompt + summary + tail messages
"""

import logging
import time
from typing import Any, Dict, List, Optional

from .token_counter import count_messages_tokens
from .history_file import HistoryFileManager
from .summarizer import Summarizer

# Use stub for standalone, real ABC when installed as plugin
try:
    from agent.context_engine import ContextEngine
except ImportError:
    from .tests.stubs.context_engine import ContextEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLD_PERCENT = 0.50  # Trigger at 50% (vs 75% in Johor)
_PROTECT_FIRST_N = 1  # ONLY system prompt (vs 3 in Johor)
_TAIL_TOKEN_RATIO = 0.20  # Protect 20% of context as recent tail
_MIN_MESSAGES_FOR_COMPRESS = 5  # Need at least this many to compress
_COMPRESS_COOLDOWN_SECONDS = 30  # Anti-thrashing cooldown


class CursorStyleEngine(ContextEngine):
    """Cursor-inspired context compression engine.

    Drop-in replacement for Johor' built-in ContextCompressor.
    Fixes the topic drift bug by not anchoring initial conversations.
    """

    def __init__(
        self,
        model: str = "",
        provider: str = "",
        base_url: str = "",
        api_key: str = "",
        summary_model: str = "",
        context_length: int = 200000,
        threshold_percent: float = _DEFAULT_THRESHOLD_PERCENT,
        api_mode: str = "",
        hermes_home: str = "",
    ):
        # Identity
        self._name = "cursor_style"

        # Model info
        self.model = model
        self.provider = provider
        self.base_url = base_url
        self.api_key = api_key
        self.api_mode = api_mode

        # Token state (required by ContextEngine ABC)
        self.last_prompt_tokens: int = 0
        self.last_completion_tokens: int = 0
        self.last_total_tokens: int = 0
        self.threshold_tokens: int = 0
        self.context_length: int = context_length
        self.compression_count: int = 0

        # Compaction parameters
        self.threshold_percent = threshold_percent
        self.protect_first_n: int = _PROTECT_FIRST_N  # ONLY system prompt
        self.protect_last_n: int = 6

        # Sub-modules
        self._summarizer = Summarizer(
            model=model,
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            summary_model=summary_model,
            context_length=context_length,
            api_mode=api_mode,
        )

        # History file manager
        history_dir = ""
        if hermes_home:
            history_dir = f"{hermes_home}/context_history"
        self._history = HistoryFileManager(base_dir=history_dir or "/tmp/hermes_context_history")

        # Session state
        self._session_id: Optional[str] = None
        self._compress_cooldown_until: float = 0

        # Anti-thrashing
        self._last_compression_savings_pct: float = 100.0
        self._ineffective_count: int = 0

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        self.last_total_tokens = usage.get("total_tokens", 0)

    def should_compress(self, prompt_tokens: int = None) -> bool:
        tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens

        if tokens <= 0:
            return False

        if tokens < self.threshold_tokens:
            return False

        # Anti-thrashing cooldown
        now = time.monotonic()
        if now < self._compress_cooldown_until:
            return False

        return True

    def should_compress_preflight(self, messages: List[Dict[str, Any]]) -> bool:
        """Quick estimate before API call."""
        if not messages:
            return False
        tokens = count_messages_tokens(messages)
        return tokens >= self.threshold_tokens

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
    ) -> List[Dict[str, Any]]:
        """Compress conversation messages.

        Algorithm:
          1. Save history to file (two-tier memory)
          2. Prune old tool outputs
          3. Find tail boundary by token budget
          4. Generate minimal summary
          5. Assemble: system + summary + tail
        """
        n = len(messages)
        if n < _MIN_MESSAGES_FOR_COMPRESS:
            return messages

        # Step 0: Save full history to file
        if self._session_id:
            try:
                self._history.save(messages, session_id=self._session_id)
            except Exception as e:
                logger.warning("Failed to save history file: %s", e)

        # Step 1: Prune old tool outputs
        messages = self._summarizer.prune_tool_outputs(
            messages, protect_tail_count=self.protect_last_n,
        )

        # Step 2: Determine boundaries
        # Only protect system prompt (index 0)
        compress_start = self.protect_first_n  # = 1 (just system)
        compress_start = self._align_boundary_forward(messages, compress_start)

        # Find tail boundary by token budget
        tail_budget = int(self.context_length * _TAIL_TOKEN_RATIO)
        compress_end = self._find_tail_cut_by_tokens(
            messages, compress_start, tail_budget,
        )

        if compress_end <= compress_start:
            return messages

        turns_to_summarize = messages[compress_start:compress_end]

        logger.info(
            "Cursor-style compression: summarizing messages %d-%d "
            "(%d of %d total), protecting %d head + %d tail",
            compress_start + 1, compress_end,
            len(turns_to_summarize), n,
            compress_start, n - compress_end,
        )

        # Step 3: Generate summary
        content_tokens = count_messages_tokens(turns_to_summarize)
        summary_budget = self._summarizer.compute_budget(content_tokens)
        summary = self._summarizer.summarize(
            turns_to_summarize, summary_budget, focus_topic=focus_topic,
        )

        # Step 4: Assemble compressed message list
        compressed = self._assemble(
            messages, compress_start, compress_end, summary,
        )

        # Step 5: Anti-thrashing check
        self._update_anti_thrashing(messages, compressed)

        self.compression_count += 1
        logger.info(
            "Compression #%d complete: %d -> %d messages",
            self.compression_count, n, len(compressed),
        )

        return compressed

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_session_start(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        hermes_home = kwargs.get("hermes_home", "")
        if hermes_home:
            self._history = HistoryFileManager(
                base_dir=f"{hermes_home}/context_history"
            )
        self._summarizer.previous_summary = None

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        self._session_id = None

    def on_session_reset(self) -> None:
        super().on_session_reset()
        self._summarizer.previous_summary = None
        self._compress_cooldown_until = 0
        self._ineffective_count = 0

    # ------------------------------------------------------------------
    # Model switch
    # ------------------------------------------------------------------

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
        **kwargs,
    ) -> None:
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent)
        self.model = model
        if base_url:
            self.base_url = base_url
        if api_key:
            self.api_key = api_key
        if provider:
            self.provider = provider
        self._summarizer.model = model
        self._summarizer.context_length = context_length

    # ------------------------------------------------------------------
    # Internal: boundary finding
    # ------------------------------------------------------------------

    def _align_boundary_forward(
        self, messages: List[Dict[str, Any]], start: int,
    ) -> int:
        """Don't cut in the middle of a tool_call/result pair."""
        while start < len(messages) - 1:
            msg = messages[start]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # Skip past all tool results for these calls
                tool_call_ids = {tc["id"] for tc in msg["tool_calls"]}
                start += 1
                while start < len(messages):
                    if messages[start].get("tool_call_id") in tool_call_ids:
                        start += 1
                    else:
                        break
            else:
                break
        return start

    def _find_tail_cut_by_tokens(
        self,
        messages: List[Dict[str, Any]],
        compress_start: int,
        tail_budget: int,
    ) -> int:
        """Find where to stop summarizing, protecting recent tail by tokens."""
        budget_remaining = tail_budget
        cut = len(messages)

        # Walk backwards from the end
        for i in range(len(messages) - 1, compress_start - 1, -1):
            msg_tokens = count_messages_tokens([messages[i]])
            if budget_remaining - msg_tokens < 0:
                cut = i + 1
                break
            budget_remaining -= msg_tokens

        # Ensure we don't cut inside a tool_call/result pair
        cut = self._align_boundary_backward(messages, cut)

        return cut

    def _align_boundary_backward(
        self, messages: List[Dict[str, Any]], end: int,
    ) -> int:
        """Don't cut leaving orphaned tool_calls without results."""
        while end < len(messages):
            msg = messages[end]
            if msg.get("role") == "tool":
                # This is a tool result — include it (and its parent call)
                end += 1
            else:
                break
        return end

    # ------------------------------------------------------------------
    # Internal: assembly
    # ------------------------------------------------------------------

    def _assemble(
        self,
        messages: List[Dict[str, Any]],
        compress_start: int,
        compress_end: int,
        summary: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Assemble the compressed message list."""
        compressed = []

        # 1. System prompt (always preserved)
        for i in range(compress_start):
            msg = messages[i].copy()
            if i == 0 and msg.get("role") == "system":
                existing = msg.get("content") or ""
                note = (
                    "\n\n[Note: Earlier conversation turns have been compacted. "
                    "Build on the current state rather than re-doing work.]"
                )
                if note not in existing:
                    msg["content"] = existing + note
            compressed.append(msg)

        # 2. Summary (or fallback)
        if summary:
            # Append history file reference if available
            if self._session_id:
                ref = self._history.get_reference_text(self._session_id)
                if ref:
                    summary = summary + "\n\n" + ref

            # Choose role to avoid consecutive same-role
            last_role = compressed[-1].get("role", "system") if compressed else "system"
            first_tail_role = (
                messages[compress_end].get("role", "user")
                if compress_end < len(messages) else "user"
            )

            if last_role == "user":
                summary_role = "assistant"
            else:
                summary_role = "user"

            # Avoid collision with tail
            if summary_role == first_tail_role:
                flipped = "assistant" if summary_role == "user" else "user"
                if flipped != last_role:
                    summary_role = flipped

            compressed.append({"role": summary_role, "content": summary})
        else:
            # Fallback: no summary available
            n_dropped = compress_end - compress_start
            fallback = (
                f"[Context compaction: {n_dropped} earlier turns were removed "
                f"to free context space. Continue based on recent messages "
                f"and the current state of files.]"
            )
            compressed.append({"role": "user", "content": fallback})

        # 3. Tail messages (preserved verbatim)
        for i in range(compress_end, len(messages)):
            compressed.append(messages[i].copy())

        return compressed

    # ------------------------------------------------------------------
    # Internal: anti-thrashing
    # ------------------------------------------------------------------

    def _update_anti_thrashing(
        self,
        original: List[Dict[str, Any]],
        compressed: List[Dict[str, Any]],
    ) -> None:
        """Track compression effectiveness and apply cooldown."""
        orig_tokens = count_messages_tokens(original)
        comp_tokens = count_messages_tokens(compressed)

        if orig_tokens > 0:
            savings_pct = (orig_tokens - comp_tokens) / orig_tokens * 100
        else:
            savings_pct = 0

        self._last_compression_savings_pct = savings_pct

        if savings_pct < 10:
            self._ineffective_count += 1
        else:
            self._ineffective_count = 0

        # Apply cooldown
        if self._ineffective_count >= 2:
            self._compress_cooldown_until = time.monotonic() + 120
            logger.warning(
                "Compression ineffective %d times in a row. "
                "Cooling down for 120s.",
                self._ineffective_count,
            )
        else:
            self._compress_cooldown_until = time.monotonic() + _COMPRESS_COOLDOWN_SECONDS


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx):
    """Register the CursorStyleEngine with JOHOR Agent's plugin system."""
    engine = CursorStyleEngine()
    ctx.register_context_engine(engine)
