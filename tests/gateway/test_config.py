"""Tests for gateway configuration management."""

import os
from unittest.mock import patch

from gateway.config import (
    GatewayConfig,
    HomeChannel,
    Platform,
    PlatformConfig,
    SessionResetPolicy,
    StreamingConfig,
    _apply_env_overrides,
    load_gateway_config,
)


class TestPlatformConfigRoundtrip:
    def test_to_dict_from_dict(self):
        pc = PlatformConfig(
            enabled=True,
            token="tok_123",
            home_channel=HomeChannel(
                platform=Platform.TELEGRAM,
                chat_id="555",
                name="Home",
            ),
            extra={"foo": "bar"},
        )
        d = pc.to_dict()
        restored = PlatformConfig.from_dict(d)

        assert restored.enabled is True
        assert restored.token == "tok_123"
        assert restored.home_channel.chat_id == "555"
        assert restored.extra == {"foo": "bar"}

    def test_disabled_no_token(self):
        pc = PlatformConfig()
        d = pc.to_dict()
        restored = PlatformConfig.from_dict(d)
        assert restored.enabled is False
        assert restored.token is None

    def test_from_dict_coerces_quoted_false_enabled(self):
        restored = PlatformConfig.from_dict({"enabled": "false"})
        assert restored.enabled is False

    def test_gateway_restart_notification_defaults_true(self):
        assert PlatformConfig().gateway_restart_notification is True
        assert PlatformConfig.from_dict({}).gateway_restart_notification is True

    def test_gateway_restart_notification_roundtrip_false(self):
        pc = PlatformConfig(enabled=True, gateway_restart_notification=False)
        restored = PlatformConfig.from_dict(pc.to_dict())
        assert restored.gateway_restart_notification is False

    def test_gateway_restart_notification_coerces_quoted_false(self):
        restored = PlatformConfig.from_dict({"gateway_restart_notification": "false"})
        assert restored.gateway_restart_notification is False


class TestSessionResetPolicy:
    def test_roundtrip(self):
        policy = SessionResetPolicy(mode="idle", at_hour=6, idle_minutes=120)
        d = policy.to_dict()
        restored = SessionResetPolicy.from_dict(d)
        assert restored.mode == "idle"
        assert restored.at_hour == 6
        assert restored.idle_minutes == 120

    def test_defaults(self):
        policy = SessionResetPolicy()
        assert policy.mode == "both"
        assert policy.at_hour == 4
        assert policy.idle_minutes == 1440

    def test_from_dict_treats_null_values_as_defaults(self):
        restored = SessionResetPolicy.from_dict(
            {"mode": None, "at_hour": None, "idle_minutes": None}
        )
        assert restored.mode == "both"
        assert restored.at_hour == 4
        assert restored.idle_minutes == 1440

    def test_from_dict_coerces_quoted_false_notify(self):
        restored = SessionResetPolicy.from_dict({"notify": "false"})
        assert restored.notify is False


class TestStreamingConfig:
    def test_from_dict_coerces_quoted_false_enabled(self):
        restored = StreamingConfig.from_dict({"enabled": "false"})
        assert restored.enabled is False

    def test_from_dict_malformed_numeric_values_fall_back_to_defaults(self):
        restored = StreamingConfig.from_dict(
            {
                "edit_interval": "oops",
                "buffer_threshold": "oops",
                "fresh_final_after_seconds": "oops",
            }
        )
        assert restored.edit_interval == 0.8
        assert restored.buffer_threshold == 24
        assert restored.fresh_final_after_seconds == 60.0
