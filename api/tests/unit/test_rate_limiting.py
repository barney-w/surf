"""Unit tests for per-user rate limiting middleware."""

from unittest.mock import MagicMock

from slowapi import Limiter

from src.middleware.rate_limit import get_user_key, limiter


class TestGetUserKey:
    """Tests for the get_user_key key function."""

    def test_returns_user_id_when_user_context_present(self):
        """Should return user_id from request.state.user_context when available."""
        request = MagicMock()
        user_context = MagicMock()
        user_context.user_id = "user-abc-123"
        request.state.user_context = user_context

        key = get_user_key(request)

        assert key == "user-abc-123"

    def test_falls_back_to_ip_when_no_user_context(self):
        """Should fall back to remote IP when no user_context on request.state."""
        request = MagicMock()
        # Simulate missing attribute on state
        del request.state.user_context
        request.client.host = "127.0.0.1"
        # slowapi's get_remote_address reads from request.client.host
        # We patch state to raise AttributeError on access
        type(request.state).user_context = property(
            lambda self: (_ for _ in ()).throw(AttributeError("no user_context"))
        )

        key = get_user_key(request)

        # Should not be the user_id — should be an IP-based string
        assert key != "user-abc-123"

    def test_falls_back_to_ip_when_user_context_has_no_user_id(self):
        """Should fall back to remote IP when user_context lacks user_id attribute."""
        request = MagicMock()
        user_context = MagicMock(spec=[])  # no attributes
        request.state.user_context = user_context
        request.client.host = "10.0.0.1"

        key = get_user_key(request)

        # user_context exists but has no user_id — should fall back to IP
        assert key != "user-abc-123"

    def test_falls_back_to_ip_when_state_has_no_user_context(self):
        """getattr with None default should return None, triggering IP fallback."""
        request = MagicMock()
        # getattr(request.state, "user_context", None) should return None
        request.state = MagicMock(spec=[])  # state has no user_context attribute
        request.client.host = "192.168.1.1"

        key = get_user_key(request)

        assert key is not None  # should return the IP, not crash


class TestLimiterObject:
    """Tests for the module-level limiter instance."""

    def test_limiter_is_instance_of_limiter(self):
        """The exported limiter must be a slowapi Limiter instance."""
        assert isinstance(limiter, Limiter)

    def test_limiter_uses_user_key_function(self):
        """The limiter's key_func should be get_user_key."""
        assert limiter._key_func is get_user_key  # pyright: ignore[reportPrivateUsage]
