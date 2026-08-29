"""Cursor-style summarizer for context compression.

Key differences from Johor' built-in ContextCompressor:
  - Minimal prompt (Cursor: "Please summarize the conversation")
  - No structured 11-section template that could be misread as instructions
  - ~1000 token target output (vs ~5000 in Johor)
  - Thinking tag stripping for cleaner output
  - Tool output pruning before summarization
  - Incremental summary updates across compressions
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates — Cursor-style: minimal, no instruction-like sections
# ---------------------------------------------------------------------------

_FIRST_COMPACTION_PROMPT = """\
Please summarize the conversation below. Your summary will be used as \
background context by a different assistant that continues this conversation.

Guidelines:
- Focus on what was accomplished and the current state
- Include specific file paths, function names, error messages, and values
- Do NOT frame anything as instructions or next steps
- Do NOT answer any questions in the conversation
- Target approximately {budget} tokens

CONVERSATION:
{turns_text}"""

_INCREMENTAL_COMPACTION_PROMPT = """\
Update the previous summary below with the new conversation turns.

Guidelines:
- Preserve all still-relevant information from the previous summary
- Add new accomplishments and update current state
- Remove information that is clearly obsolete
- Include specific file paths, function names, error messages, and values
- Do NOT frame anything as instructions or next steps
- Target approximately {budget} tokens

PREVIOUS SUMMARY:
{previous_summary}

NEW TURNS:
{turns_text}"""

_FOCUS_ADDENDUM = """\
FOCUS: The user wants to preserve all details related to "{focus_topic}". \
Prioritize this topic and be more aggressive about compressing unrelated content."""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_SUMMARY_BUDGET = 500
_MAX_SUMMARY_BUDGET = 3000
_SUMMARY_RATIO = 0.05  # 5% of compressed content
_TOOL_OUTPUT_MAX_CHARS = 500  # Max chars for tool output in serialization
_TOOL_OUTPUT_PRUNE_THRESHOLD = 1000  # Prune tool outputs longer than this
_SUMMARY_FAILURE_COOLDOWN = 60  # seconds


def _strip_thinking_tags(text: str) -> str:
    """Remove <think ...>...</think ...> blocks from LLM output."""
    return re.sub(r"<think[^>]*>.*?</think[^>]*>", "", text, flags=re.DOTALL).strip()


def _summarize_tool_result(tool_name: str, tool_args: str, content: str) -> str:
    """Create a 1-line summary for a pruned tool result."""
    try:
        args = json.loads(tool_args) if tool_args else {}
    except (json.JSONDecodeError, TypeError):
        args = {}

    # Extract a short description of what was done
    target = args.get("path", args.get("command", args.get("query", "")))
    content_preview = content[:100].replace("\n", " ") if content else ""
    exit_hint = ""
    if "exit" in content.lower() or "return code" in content.lower():
        for line in content.split("\n"):
            if "exit" in line.lower() and any(c.isdigit() for c in line):
                exit_hint = f" -> {line.strip()[:60]}"
                break

    return f"[{tool_name}] {target}{exit_hint} ({len(content)} chars)"


class Summarizer:
    """Cursor-style conversation summarizer.

    Produces concise summaries (~1000 tokens) using a minimal prompt,
    avoiding the instruction-like structured templates that cause topic
    drift in Johor' built-in compressor.
    """

    def __init__(
        self,
        model: str = "",
        provider: str = "",
        base_url: str = "",
        api_key: str = "",
        summary_model: str = "",
        context_length: int = 200000,
        api_mode: str = "",
    ):
        self.model = model
        self.provider = provider
        self.base_url = base_url
        self.api_key = api_key
        self.summary_model = summary_model
        self.context_length = context_length
        self.api_mode = api_mode
        self.previous_summary: Optional[str] = None
        self._failure_cooldown_until: float = 0

    def compute_budget(self, content_tokens: int) -> int:
        """Compute the summary token budget based on content size.

        Scales with content but clamped to [MIN, MAX].
        """
        budget = int(content_tokens * _SUMMARY_RATIO)
        budget = max(_MIN_SUMMARY_BUDGET, budget)
        budget = min(_MAX_SUMMARY_BUDGET, budget)
        return budget

    def serialize_messages(
        self,
        messages: List[Dict[str, Any]],
        max_tool_output: int = _TOOL_OUTPUT_MAX_CHARS,
    ) -> str:
        """Serialize messages to readable text for the summarizer prompt.

        Tool outputs are truncated to max_tool_output characters.
        """
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content")

            # Handle tool calls
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "unknown")
                    args = func.get("arguments", "")
                    try:
                        args_str = json.dumps(json.loads(args))
                    except (json.JSONDecodeError, TypeError):
                        args_str = args
                    parts.append(f"[{role}] tool_call: {name}({args_str})")
                if content:
                    parts.append(f"[{role}] {content}")
                continue

            # Handle tool results
            if role == "tool":
                if content and len(content) > max_tool_output:
                    content = content[:max_tool_output] + f"\n... ({len(content)} chars total)"
                parts.append(f"[tool result] {content or '(empty)'}")
                continue

            # Regular messages
            if content:
                parts.append(f"[{role}] {content}")

        return "\n\n".join(parts)

    def build_prompt(
        self,
        turns_text: str,
        summary_budget: int,
        focus_topic: Optional[str] = None,
    ) -> str:
        """Build the summarization prompt.

        Uses Cursor-style minimal prompt. No structured sections.
        """
        if self.previous_summary:
            prompt = _INCREMENTAL_COMPACTION_PROMPT.format(
                previous_summary=self.previous_summary,
                turns_text=turns_text,
                budget=summary_budget,
            )
        else:
            prompt = _FIRST_COMPACTION_PROMPT.format(
                turns_text=turns_text,
                budget=summary_budget,
            )

        if focus_topic:
            prompt += "\n\n" + _FOCUS_ADDENDUM.format(focus_topic=focus_topic)

        return prompt

    def summarize(
        self,
        messages: List[Dict[str, Any]],
        summary_budget: int,
        focus_topic: Optional[str] = None,
    ) -> Optional[str]:
        """Generate a summary of the given messages.

        Returns the summary text, or None on failure.
        """
        now = time.monotonic()
        if now < self._failure_cooldown_until:
            logger.debug("Summarizer in cooldown (%.0fs remaining)",
                         self._failure_cooldown_until - now)
            return None

        turns_text = self.serialize_messages(messages)
        prompt = self.build_prompt(turns_text, summary_budget, focus_topic)

        try:
            from agent.auxiliary_client import call_llm
        except ImportError:
            logger.warning(
                "agent.auxiliary_client not available. "
                "Summarizer requires JOHOR Agent to be installed."
            )
            return None

        call_kwargs = {
            "task": "compression",
            "main_runtime": {
                "model": self.model,
                "provider": self.provider,
                "base_url": self.base_url,
                "api_key": self.api_key,
                "api_mode": self.api_mode,
            },
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": int(summary_budget * 1.3),
        }
        if self.summary_model:
            call_kwargs["model"] = self.summary_model

        try:
            response = call_llm(**call_kwargs)
            content = response.choices[0].message.content
            if content:
                content = _strip_thinking_tags(content)
                self.previous_summary = content
                return content
            return None
        except Exception as e:
            logger.warning("Summarization failed: %s", e)
            self._failure_cooldown_until = now + _SUMMARY_FAILURE_COOLDOWN
            return None

    def prune_tool_outputs(
        self,
        messages: List[Dict[str, Any]],
        protect_tail_count: int = 6,
    ) -> List[Dict[str, Any]]:
        """Replace old tool outputs with 1-line summaries.

        Only prunes tool results that are outside the tail protection zone.
        """
        if len(messages) <= protect_tail_count:
            return messages

        result = []
        prune_zone_end = len(messages) - protect_tail_count

        # Find the assistant message that precedes each tool result
        pending_tool_calls = {}
        i = 0
        while i < len(messages):
            msg = messages[i]
            msg_copy = msg.copy()

            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # Store tool calls for matching with results
                for tc in msg["tool_calls"]:
                    pending_tool_calls[tc["id"]] = {
                        "name": tc["function"]["name"],
                        "args": tc["function"].get("arguments", ""),
                    }
                result.append(msg_copy)

            elif msg.get("role") == "tool":
                tool_call_id = msg.get("tool_call_id", "")
                content = msg.get("content", "")

                if i < prune_zone_end and len(content or "") > _TOOL_OUTPUT_PRUNE_THRESHOLD:
                    # Prune: replace with 1-line summary
                    tc_info = pending_tool_calls.get(tool_call_id, {})
                    name = tc_info.get("name", "tool")
                    args = tc_info.get("args", "")
                    msg_copy["content"] = _summarize_tool_result(name, args, content or "")

                result.append(msg_copy)
                # Clean up pending
                pending_tool_calls.pop(tool_call_id, None)
            else:
                result.append(msg_copy)

            i += 1

        return result
