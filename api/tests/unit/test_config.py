"""Tests for the config/settings module."""

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
