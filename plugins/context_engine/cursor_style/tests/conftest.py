"""Shared test configuration and fixtures."""

import sys
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CURSOR_STYLE_DIR = PROJECT_ROOT / "cursor_style"
STUBS_DIR = CURSOR_STYLE_DIR / "tests" / "stubs"


# ---------------------------------------------------------------------------
# Ensure stubs are importable for unit tests (no Johor dependency)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _add_stubs_to_path():
    """Add stubs directory to sys.path so unit tests can import the ABC stub."""
    stubs_str = str(STUBS_DIR)
    if stubs_str not in sys.path:
        sys.path.insert(0, stubs_str)


# ---------------------------------------------------------------------------
# Sample message fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_system_message():
    return {"role": "system", "content": "You are a helpful coding assistant."}


@pytest.fixture
def sample_user_message():
    return {"role": "user", "content": "Help me refactor the auth module."}


@pytest.fixture
def sample_assistant_message():
    return {
        "role": "assistant",
        "content": "I'll start by examining the existing auth code.",
        "tool_calls": [
            {
                "id": "call_001",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "src/auth/login.py"}',
                },
            }
        ],
    }


@pytest.fixture
def sample_tool_message():
    return {
        "role": "tool",
        "tool_call_id": "call_001",
        "content": "def login(username, password):\n    # TODO: refactor this\n    user = db.find_user(username)\n    if user and user.password == password:\n        return True\n    return False\n",
    }


@pytest.fixture
def short_conversation(sample_system_message, sample_user_message,
                       sample_assistant_message, sample_tool_message):
    """A minimal conversation: system + user + assistant(tool_call) + tool."""
    return [
        sample_system_message,
        sample_user_message,
        sample_assistant_message,
        sample_tool_message,
    ]


@pytest.fixture
def long_conversation(short_conversation):
    """A longer conversation with many turns, suitable for compression."""
    messages = list(short_conversation)
    for i in range(20):
        messages.append({
            "role": "assistant",
            "content": f"I'm working on step {i+1} of the refactoring. "
                       f"Let me examine more files and make changes.",
            "tool_calls": [
                {
                    "id": f"call_{i+100:03d}",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": f'{{"command": "cat src/auth/file{i}.py"}}',
                    },
                }
            ],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": f"call_{i+100:03d}",
            "content": f"File content of file{i}.py - " + "x" * 500,
        })
        messages.append({"role": "user", "content": f"Continue with step {i+1}."})
        messages.append({
            "role": "assistant",
            "content": f"Done with step {i+1}. Moving to step {i+2}.",
        })
    return messages


@pytest.fixture
def chinese_conversation():
    """A conversation with Chinese content for multi-language token testing."""
    return [
        {"role": "system", "content": "你是一个编程助手。"},
        {"role": "user", "content": "帮我重构认证模块，使用JWT替代session。"},
        {"role": "assistant", "content": "好的，我先看看现有的认证代码结构。"},
        {"role": "user", "content": "请确保向后兼容，现有的session不能立刻失效。"},
        {"role": "assistant", "content": "明白，我会采用渐进式迁移策略。"},
    ]
