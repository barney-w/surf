"""Per-user rate limiting using slowapi."""
import logging

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)


def _get_user_key(request: Request) -> str:
    """Extract rate limit key from authenticated user or fall back to IP."""
    # user_context is set by the auth dependency in chat routes
    user = getattr(request.state, "user_context", None)
    if user and hasattr(user, "user_id"):
        return user.user_id
    return get_remote_address(request)


limiter = Limiter(key_func=_get_user_key)
