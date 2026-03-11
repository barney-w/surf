import os

# Disable auth for unit tests so endpoints don't require JWT tokens.
# Tests that specifically test auth behaviour mock get_settings directly.
os.environ.setdefault("AUTH_ENABLED", "false")
