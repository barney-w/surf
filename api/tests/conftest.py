import os

# Disable auth for unit tests so endpoints don't require JWT tokens.
# Tests that specifically test auth behaviour mock get_settings directly.
os.environ.setdefault("AUTH_ENABLED", "false")

# Provide a dummy POSTGRES_PASSWORD so Settings validation passes during
# module-level imports (e.g. `from src.main import app`).  Tests that need
# a real database override via DATABASE_URL; unit tests never connect.
os.environ.setdefault("POSTGRES_PASSWORD", "test")
