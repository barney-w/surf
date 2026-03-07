"""Tests for the config/settings module."""

import pytest

from src.config.settings import Settings, get_settings


class TestSettingsDefaults:
    """Test that Settings loads with sensible defaults when no env vars are set."""

    def test_default_values(self):
        settings = Settings(_env_file=None)

        assert settings.environment == "dev"
        assert settings.log_level == "INFO"
        assert settings.debug is False
        assert settings.azure_openai_endpoint == ""
        assert settings.azure_openai_embedding_deployment_name == "text-embedding-3-large"
        assert settings.azure_openai_api_version == "2024-12-01-preview"
        assert settings.anthropic_api_key == ""
        assert settings.anthropic_model_id == "claude-sonnet-4-6"
        assert settings.azure_search_endpoint == ""
        assert settings.azure_search_index_name == "surf-index"
        assert settings.cosmos_endpoint == ""
        assert settings.cosmos_database_name == "surf"
        assert settings.cosmos_container_name == "conversations"
        assert settings.azure_storage_account_url == ""
        assert settings.azure_keyvault_url == ""
        assert settings.auth_enabled is False
        assert settings.entra_tenant_id == ""
        assert settings.entra_client_id == ""
        assert settings.api_cors_origins == ["http://localhost:3000"]
        assert settings.max_history_messages == 20

    def test_get_settings_returns_settings_instance(self):
        get_settings.cache_clear()
        settings = get_settings()
        assert isinstance(settings, Settings)


class TestSettingsEnvOverrides:
    """Test that environment variables override default values."""

    def test_env_var_overrides(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my-oai.openai.azure.com")
        monkeypatch.setenv("AZURE_SEARCH_INDEX_NAME", "custom-index")
        monkeypatch.setenv("COSMOS_DATABASE_NAME", "my-db")
        monkeypatch.setenv("MAX_HISTORY_MESSAGES", "50")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("API_CORS_ORIGINS", '["http://localhost:5173"]')

        settings = Settings()

        assert settings.environment == "production"
        assert settings.log_level == "DEBUG"
        assert settings.debug is True
        assert settings.azure_openai_endpoint == "https://my-oai.openai.azure.com"
        assert settings.azure_search_index_name == "custom-index"
        assert settings.cosmos_database_name == "my-db"
        assert settings.max_history_messages == 50
        assert settings.auth_enabled is True
        assert settings.api_cors_origins == ["http://localhost:5173"]


class TestLifespanProductionGuards:
    """Test that the lifespan function enforces production safety guards."""

    @pytest.mark.asyncio
    async def test_prod_refuses_auth_disabled(self):
        """Staging environment with auth_enabled=False must raise SystemExit."""
        from unittest.mock import patch

        from src.config.settings import Settings
        from src.main import app, lifespan

        unsafe_settings = Settings(
            _env_file=None,
            environment="staging",
            auth_enabled=False,
        )

        with patch("src.main.settings", unsafe_settings), pytest.raises(SystemExit):
            async with lifespan(app):
                pass

    @pytest.mark.asyncio
    async def test_prod_refuses_debug_mode(self):
        """Prod environment with debug=True must raise SystemExit."""
        from unittest.mock import patch

        from src.config.settings import Settings
        from src.main import app, lifespan

        unsafe_settings = Settings(
            _env_file=None,
            environment="prod",
            auth_enabled=True,
            debug=True,
        )

        with patch("src.main.settings", unsafe_settings), pytest.raises(SystemExit):
            async with lifespan(app):
                pass

    @pytest.mark.asyncio
    async def test_prod_refuses_cors_wildcard(self):
        """Prod environment with CORS wildcard must raise SystemExit."""
        from unittest.mock import patch

        from src.config.settings import Settings
        from src.main import app, lifespan

        unsafe_settings = Settings(
            _env_file=None,
            environment="prod",
            auth_enabled=True,
            debug=False,
            api_cors_origins=["*"],
        )

        with patch("src.main.settings", unsafe_settings), pytest.raises(SystemExit):
            async with lifespan(app):
                pass

    @pytest.mark.asyncio
    async def test_dev_allows_auth_disabled(self):
        """Dev environment with auth_enabled=False must NOT raise."""
        from unittest.mock import patch

        from src.config.settings import Settings
        from src.main import app, lifespan

        dev_settings = Settings(
            _env_file=None,
            environment="dev",
            auth_enabled=False,
        )

        # The lifespan may fail later (no Azure creds in CI) but it must NOT
        # raise SystemExit from the safety-guard block.  We catch everything
        # except SystemExit so the test fails only on the guard.
        with patch("src.main.settings", dev_settings):
            try:
                async with lifespan(app):
                    pass
            except SystemExit:
                pytest.fail("SystemExit raised for dev environment — guard should not fire")
            except Exception:
                pass  # other startup errors (missing Azure creds etc.) are fine

    @pytest.mark.asyncio
    async def test_prod_allows_valid_config(self):
        """Prod environment with valid config must NOT raise SystemExit from guards."""
        from unittest.mock import patch

        from src.config.settings import Settings
        from src.main import app, lifespan

        valid_settings = Settings(
            _env_file=None,
            environment="prod",
            auth_enabled=True,
            debug=False,
            api_cors_origins=["https://surf.example.com"],
        )

        with patch("src.main.settings", valid_settings):
            try:
                async with lifespan(app):
                    pass
            except SystemExit:
                pytest.fail("SystemExit raised for valid prod config — guard should not fire")
            except Exception:
                pass  # other startup errors (missing Azure creds etc.) are fine
