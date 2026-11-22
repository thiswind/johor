"""Tests for token_counter module.

TDD Red phase: these tests define the expected behavior.
They should FAIL until token_counter.py is implemented.
"""

import pytest


class TestTokenCounter:
    """Tests for count_tokens() function."""

    def test_empty_string_returns_zero(self):
        from cursor_style.token_counter import count_tokens
        assert count_tokens("") == 0

    def test_single_word_english(self):
        from cursor_style.token_counter import count_tokens
        # "hello" should be 1 token
        tokens = count_tokens("hello")
        assert tokens == 1

    def test_short_english_sentence(self):
        from cursor_style.token_counter import count_tokens
        tokens = count_tokens("Hello, world!")
        # Should be a small number of tokens (2-4)
        assert 1 <= tokens <= 5

    def test_long_english_text(self):
        from cursor_style.token_counter import count_tokens
        text = "The quick brown fox jumps over the lazy dog. " * 100
        tokens = count_tokens(text)
        # ~1350 chars -> tiktoken gives ~1001 tokens (repetition not compressed)
        assert 500 <= tokens <= 1500

    def test_chinese_text_not_underestimated(self):
        """Chinese text should NOT be estimated as chars/4.

        The old Johor approach (len(text)//4) severely underestimates
        Chinese tokens. Our counter should be much more accurate.
        """
        from cursor_style.token_counter import count_tokens
        text = "人工智能正在改变世界，深度学习技术已经广泛应用于自然语言处理和计算机视觉领域。"
        tokens = count_tokens(text)
        naive_estimate = len(text) // 4  # old approach
        # Our estimate should be significantly higher than naive
        # Chinese: ~1.5-2 chars per token, naive assumes 4 chars per token
        assert tokens > naive_estimate

    def test_mixed_chinese_english(self):
        from cursor_style.token_counter import count_tokens
        text = "请使用 Python 的 requests 库来发送 HTTP 请求，并处理 JSON 响应。"
        tokens = count_tokens(text)
        assert tokens > 0

    def test_code_snippet(self):
        from cursor_style.token_counter import count_tokens
        code = '''
def authenticate(username: str, password: str) -> bool:
    """Check user credentials against the database."""
    user = db.query(User).filter_by(username=username).first()
    if user and verify_password(password, user.password_hash):
        return True
    return False
'''
        tokens = count_tokens(code)
        assert tokens > 0

    def test_messages_list(self):
        from cursor_style.token_counter import count_messages_tokens
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there! How can I help?"},
        ]
        tokens = count_messages_tokens(messages)
        assert tokens > 0

    def test_messages_with_tool_calls(self):
        from cursor_style.token_counter import count_messages_tokens
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [
                {
                    "id": "call_001",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "/src/auth.py"}',
                    },
                }
            ]},
            {"role": "tool", "tool_call_id": "call_001",
             "content": "def auth(): pass"},
        ]
        tokens = count_messages_tokens(messages)
        assert tokens > 0

    def test_messages_empty_list(self):
        from cursor_style.token_counter import count_messages_tokens
        assert count_messages_tokens([]) == 0

    def test_count_tokens_returns_int(self):
        from cursor_style.token_counter import count_tokens
        assert isinstance(count_tokens("hello"), int)

    def test_consistency_same_input_same_output(self):
        """Same input should always produce the same token count."""
        from cursor_style.token_counter import count_tokens
        text = "Consistency test for token counting."
        assert count_tokens(text) == count_tokens(text)

    def test_japanese_text(self):
        from cursor_style.token_counter import count_tokens
        text = "プログラミングは楽しいです。機械学習のモデルを訓練しています。"
        tokens = count_tokens(text)
        naive_estimate = len(text) // 4
        assert tokens > naive_estimate

    def test_korean_text(self):
        from cursor_style.token_counter import count_tokens
        text = "인공지능 기술이 빠르게 발전하고 있습니다."
        tokens = count_tokens(text)
        naive_estimate = len(text) // 4
        assert tokens > naive_estimate

    def test_overhead_per_message(self):
        """Each message has a small token overhead (role, formatting)."""
        from cursor_style.token_counter import count_messages_tokens, _MESSAGE_OVERHEAD
        # A single empty message should have at least the overhead
        empty_msg = [{"role": "user", "content": ""}]
        tokens = count_messages_tokens(empty_msg)
        assert tokens >= _MESSAGE_OVERHEAD
