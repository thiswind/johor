"""Cursor-style context compression engine for JOHOR Agent.

A drop-in plugin that replaces the built-in ContextCompressor with a
Cursor-inspired approach:

  - No initial conversation anchoring (fixes topic drift)
  - Minimal summarization prompt (~1000 tokens output)
  - History file mechanism for detail recovery
  - Precise multi-language token counting
  - Tool output pruning before LLM summarization
  - Incremental summary updates across multiple compressions

Installation:
  1. Copy this directory to hermes-agent/plugins/context_engine/cursor_style/
  2. Set context.engine: "cursor_style" in cli-config.yaml
"""

__version__ = "0.1.0"

# Export register function for Johor plugin system
from .engine import register

__all__ = ["register"]
