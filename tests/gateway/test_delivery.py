"""Tests for the delivery routing module."""

from gateway.config import Platform
from gateway.delivery import DeliveryTarget
from gateway.session import SessionSource


class TestPlatformNameCaseInsensitivity:
    """Test that platform names are case-insensitive."""
    
    def test_uppercase_platform_name(self):
        """Platform names should be case-insensitive."""
        target = DeliveryTarget.parse("TELEGRAM:12345")
        assert target.platform == Platform.TELEGRAM
        assert target.chat_id == "12345"
    
    def test_mixed_case_platform_name(self):
        """Mixed-case platform names should work."""
        target = DeliveryTarget.parse("TeleGram:12345")
        assert target.platform == Platform.TELEGRAM
        assert target.chat_id == "12345"
