from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Environment
    environment: str = "dev"
    log_level: str = "INFO"
    debug: bool = False

    # Azure OpenAI (embeddings only — chat model replaced by Anthropic)
    azure_openai_endpoint: str = ""
    azure_openai_embedding_deployment_name: str = "text-embedding-3-large"
    azure_openai_api_version: str = "2024-12-01-preview"

    # Anthropic (direct API or via Azure AI Foundry)
    anthropic_api_key: str = ""
    anthropic_model_id: str = "claude-haiku-4-5-20251001"
    anthropic_domain_model_id: str = "claude-sonnet-4-6"
    anthropic_foundry_base_url: str = ""
    anthropic_foundry_api_key: str = ""
    anthropic_proofread_model_id: str = "claude-haiku-4-5-20251001"
    proofread_enabled: bool = True

    # Azure AI Search
    azure_search_endpoint: str = ""
    azure_search_index_name: str = "surf-index"
    azure_search_sharepoint_index: str = ""

    # PostgreSQL
    postgres_enabled: bool = True
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_database: str = "surf"
    postgres_user: str = "surf"
    postgres_password: str = "localdev"
    postgres_ssl: bool = True

    # Azure Storage
    azure_storage_account_url: str = ""

    # Azure Key Vault
    azure_keyvault_url: str = ""

    # Auth
    auth_enabled: bool = False
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""
    guest_token_secret: str = ""
    guest_token_ttl_minutes: int = 30

    # Organisation
    organisation_name: str = ""

    # API
    api_cors_origins: list[str] = [
        "http://localhost:3000",
        "https://tauri.localhost",
        "http://localhost:8081",  # Expo dev server
    ]

    # Conversation
    max_history_messages: int = 20

    model_config = {"env_prefix": "", "env_file": ("../.env", ".env"), "extra": "ignore"}

    @model_validator(mode="after")
    def _validate_production_keys(self) -> "Settings":
        if (
            self.environment != "dev"
            and not self.anthropic_api_key
            and not self.anthropic_foundry_api_key
        ):
            raise ValueError(
                "ANTHROPIC_API_KEY or ANTHROPIC_FOUNDRY_API_KEY required in non-dev environments"
            )
        if (
            self.environment != "dev"
            and self.postgres_enabled
            and self.postgres_password == "localdev"
        ):
            raise ValueError(
                "Default postgres password 'localdev' must not be used in non-dev environments"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
