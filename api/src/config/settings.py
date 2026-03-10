from functools import lru_cache

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
    anthropic_model_id: str = "claude-sonnet-4-6"
    anthropic_foundry_base_url: str = ""
    anthropic_foundry_api_key: str = ""

    # Azure AI Search
    azure_search_endpoint: str = ""
    azure_search_index_name: str = "surf-index"
    azure_search_sharepoint_index: str = ""

    # Cosmos DB
    cosmos_endpoint: str = ""
    cosmos_database_name: str = "surf"
    cosmos_container_name: str = "conversations"

    # Azure Storage
    azure_storage_account_url: str = ""

    # Azure Key Vault
    azure_keyvault_url: str = ""

    # Auth
    auth_enabled: bool = False
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""

    # API
    api_cors_origins: list[str] = ["http://localhost:3000", "https://tauri.localhost"]

    # Conversation
    max_history_messages: int = 20

    model_config = {"env_prefix": "", "env_file": ("../.env", ".env"), "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
