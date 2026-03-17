"""Tests for the config/settings module."""

import pytest

from src.config.settings import Settings, get_settings


class TestSettingsDefaults:
    """Test that Settings loads with sensible defaults when no env vars are set."""

    def test_default_values(self):
        settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

        assert settings.environment == "dev"
        assert settings.log_level == "INFO"
        assert settings.debug is False
        assert settings.azure_openai_endpoint == ""
        assert settings.azure_openai_embedding_deployment_name == "text-embedding-3-large"
        assert settings.azure_openai_api_version == "2024-12-01-preview"
        assert settings.anthropic_api_key == ""
        assert settings.anthropic_model_id == "claude-haiku-4-5-20251001"
        assert settings.anthropic_foundry_base_url == ""
        assert settings.anthropic_foundry_api_key == ""
        assert settings.azure_search_endpoint == ""
        assert settings.azure_search_index_name == "surf-index"
        assert settings.postgres_host == "localhost"
        assert settings.postgres_port == 5432
        assert settings.postgres_database == "surf"
        assert settings.postgres_user == "surf"
        assert settings.postgres_password == "localdev"
        assert settings.postgres_ssl is True
        assert settings.azure_storage_account_url == ""
        assert settings.azure_keyvault_url == ""
        assert settings.auth_enabled is False
        assert settings.entra_tenant_id == ""
        assert settings.entra_client_id == ""
        assert settings.api_cors_origins == [
            "http://localhost:3000",
            "https://tauri.localhost",
            "http://localhost:8081",
        ]
        assert settings.max_history_messages == 20

    def test_get_settings_returns_settings_instance(self):
        get_settings.cache_clear()
        settings = get_settings()
        assert isinstance(settings, Settings)


class TestProductionKeyValidator:
    """Test the model validator that requires Anthropic keys in non-dev environments."""

    def test_dev_allows_no_keys(self):
        """Dev environment should not require any Anthropic keys."""
        settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
        assert settings.environment == "dev"
        assert settings.anthropic_api_key == ""
        assert settings.anthropic_foundry_api_key == ""

    def test_non_dev_requires_anthropic_key(self):
        """Non-dev environment with no Anthropic keys must raise ValueError."""
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY or ANTHROPIC_FOUNDRY_API_KEY"):
            Settings(
                _env_file=None,  # pyright: ignore[reportCallIssue]
                environment="production",
            )

    def test_non_dev_accepts_api_key(self):
        """Non-dev environment with anthropic_api_key set should pass."""
        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            environment="production",
            anthropic_api_key="sk-test-key",
        )
        assert settings.environment == "production"

    def test_non_dev_accepts_foundry_key(self):
        """Non-dev environment with anthropic_foundry_api_key set should pass."""
        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            environment="staging",
            anthropic_foundry_api_key="sk-foundry-key",
        )
        assert settings.environment == "staging"


class TestDomainModelId:
    """Test the anthropic_domain_model_id setting."""

    def test_domain_model_id_defaults_to_sonnet(self):
        settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
        assert settings.anthropic_domain_model_id == "claude-sonnet-4-6"

    def test_domain_model_id_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ANTHROPIC_DOMAIN_MODEL_ID", "claude-haiku-4-5")
        settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
        assert settings.anthropic_domain_model_id == "claude-haiku-4-5"


class TestSettingsEnvOverrides:
    """Test that environment variables override default values."""

    def test_env_var_overrides(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my-oai.openai.azure.com")
        monkeypatch.setenv("AZURE_SEARCH_INDEX_NAME", "custom-index")
        monkeypatch.setenv("POSTGRES_DATABASE", "my-db")
        monkeypatch.setenv("MAX_HISTORY_MESSAGES", "50")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("API_CORS_ORIGINS", '["http://localhost:5173"]')
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        settings = Settings()

        assert settings.environment == "production"
        assert settings.log_level == "DEBUG"
        assert settings.debug is True
        assert settings.azure_openai_endpoint == "https://my-oai.openai.azure.com"
        assert settings.azure_search_index_name == "custom-index"
        assert settings.postgres_database == "my-db"
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
            _env_file=None,  # pyright: ignore[reportCallIssue]
            environment="staging",
            auth_enabled=False,
            anthropic_api_key="sk-test-key",
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
            _env_file=None,  # pyright: ignore[reportCallIssue]
            environment="prod",
            auth_enabled=True,
            debug=True,
            anthropic_api_key="sk-test-key",
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
            _env_file=None,  # pyright: ignore[reportCallIssue]
            environment="prod",
            auth_enabled=True,
            debug=False,
            api_cors_origins=["*"],
            anthropic_api_key="sk-test-key",
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
            _env_file=None,  # pyright: ignore[reportCallIssue]
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
            _env_file=None,  # pyright: ignore[reportCallIssue]
            environment="prod",
            auth_enabled=True,
            debug=False,
            api_cors_origins=["https://surf.example.com"],
            anthropic_api_key="sk-test-key",
        )

        with patch("src.main.settings", valid_settings):
            try:
                async with lifespan(app):
                    pass
            except SystemExit:
                pytest.fail("SystemExit raised for valid prod config — guard should not fire")
            except Exception:
                pass  # other startup errors (missing Azure creds etc.) are fine
