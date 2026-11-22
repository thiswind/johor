"""Precise multi-language token counting.

Uses tiktoken (OpenAI's tokenizer) for accurate token estimation.
Falls back to a character-ratio heuristic if tiktoken is unavailable.

Key improvement over Johor' built-in approach (len(text)//4):
  - Chinese/CJK: ~1.5-2 chars/token (not 4)
  - Japanese/Korean: similar CJK ratios
  - English: ~4 chars/token (same as before)
  - Code: handled naturally by cl100k_base encoding
"""

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Token overhead per message for role, separators, etc.
# OpenAI format: ~4 tokens per message (role + content separators)
_MESSAGE_OVERHEAD = 4

# Token overhead per tool_call entry
_TOOL_CALL_OVERHEAD = 8

# Cache the tokenizer singleton
_tokenizer = None
_tokenizer_name = None


def _get_tokenizer(encoding_name: str = "cl100k_base"):
    """Get or create a cached tiktoken tokenizer.

    cl100k_base is used by GPT-4, GPT-3.5-turbo, and is a reasonable
    approximation for Claude/Gemini token counts. It handles CJK
    characters much better than the naive chars/4 approach.
    """
    global _tokenizer, _tokenizer_name
    if _tokenizer is not None and _tokenizer_name == encoding_name:
        return _tokenizer

    try:
        import tiktoken
        _tokenizer = tiktoken.get_encoding(encoding_name)
        _tokenizer_name = encoding_name
        logger.debug("Loaded tiktoken encoding: %s", encoding_name)
        return _tokenizer
    except ImportError:
        logger.warning(
            "tiktoken not installed. Falling back to character-ratio "
            "estimation. Install with: pip install tiktoken"
        )
        return None
    except Exception as e:
        logger.warning("Failed to load tiktoken: %s. Using fallback.", e)
        return None


def _fallback_count(text: str) -> int:
    """Fallback token estimation when tiktoken is unavailable.

    Uses character-class-aware ratios instead of a flat chars/4:
      - CJK characters: ~1.5 chars/token
      - Other Unicode (emoji, etc.): ~2 chars/token
      - ASCII: ~4 chars/token
    """
    if not text:
        return 0

    cjk = 0
    other_unicode = 0
    ascii_chars = 0

    for ch in text:
        cp = ord(ch)
        if cp >= 0x4E00 and cp <= 0x9FFF:  # CJK Unified Ideographs
            cjk += 1
        elif cp >= 0x3040 and cp <= 0x30FF:  # Hiragana + Katakana
            cjk += 1
        elif cp >= 0xAC00 and cp <= 0xD7AF:  # Korean Hangul
            cjk += 1
        elif cp >= 0xF900 and cp <= 0xFAFF:  # CJK Compatibility
            cjk += 1
        elif cp > 127:
            other_unicode += 1
        else:
            ascii_chars += 1

    tokens = 0
    tokens += cjk / 1.5
    tokens += other_unicode / 2.0
    tokens += ascii_chars / 4.0
    return int(tokens) + 1


def count_tokens(text: str) -> int:
    """Count tokens in a text string.

    Uses tiktoken (cl100k_base) for precise counting. Falls back to
    character-ratio heuristic if tiktoken is unavailable.

    Args:
        text: The text to count tokens for.

    Returns:
        Integer token count.
    """
    if not text:
        return 0

    enc = _get_tokenizer()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass

    return _fallback_count(text)


def count_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Count total tokens for an OpenAI-format message list.

    Includes per-message overhead (role, separators) and tool_call
    token costs.

    Args:
        messages: List of OpenAI-format message dicts.

    Returns:
        Total estimated token count.
    """
    if not messages:
        return 0

    total = 0
    for msg in messages:
        total += _MESSAGE_OVERHEAD

        # Content tokens
        content = msg.get("content")
        if content and isinstance(content, str):
            total += count_tokens(content)
        elif content and isinstance(content, list):
            # Multi-part content (e.g., image + text)
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += count_tokens(part.get("text", ""))

        # Tool calls (on assistant messages)
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                total += _TOOL_CALL_OVERHEAD
                func = tc.get("function", {})
                total += count_tokens(func.get("name", ""))
                args = func.get("arguments", "")
                if args:
                    try:
                        # Count tokens on formatted JSON for accuracy
                        total += count_tokens(json.dumps(
                            json.loads(args), separators=(",", ":")
                        ))
                    except (json.JSONDecodeError, TypeError):
                        total += count_tokens(args)

    return total
