"""Create the Azure AI Search indexer pipeline for SharePoint content in blob storage.

Creates (via REST API, idempotent PUT):
1. Data source — blob storage connection with managed identity
2. Index — surf-sharepoint-index with vector search
3. Skillset — Text Split + Azure OpenAI Embedding
4. Indexer — blob indexer with hourly schedule (configurable via INDEXER_SCHEDULE_INTERVAL)

All endpoints, deployment names, and config come from environment variables.

Usage:
    cd ingestion && uv run python scripts/setup_sharepoint_indexer.py
    cd ingestion && uv run python scripts/setup_sharepoint_indexer.py --teardown
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import click
import httpx
from dotenv import load_dotenv

from scripts.search_api import SearchApiClient, get_env

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

_EMBEDDING_DEPLOYMENT_DEFAULT = "text-embedding-3-large"
_EMBEDDING_MODEL = "text-embedding-3-large"
_INDEXED_EXTENSIONS = (
    ".pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.html,.htm,.csv,.md,.rtf,.msg,.xml,.odt,.ods,.odp,.txt"
)


def _extract_storage_account_name(storage_url: str) -> str:
    """Extract the account name from a blob storage URL."""
    hostname = urlparse(storage_url).hostname or ""
    return hostname.split(".")[0]


def _get_subscription_id() -> str:
    """Get the current Azure subscription ID.

    Prefers AZURE_SUBSCRIPTION_ID env var. Falls back to ``az account show``
    for local development convenience.
    """
    from_env = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    if from_env:
        click.echo("  Using AZURE_SUBSCRIPTION_ID from environment.")
        return from_env

    click.echo("  AZURE_SUBSCRIPTION_ID not set — falling back to az CLI.")
    result = subprocess.run(  # noqa: S603, S607
        ["az", "account", "show", "--query", "id", "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _get_resource_group() -> str:
    """Get the resource group from env or default."""
    return os.environ.get("AZURE_RESOURCE_GROUP", "rg-surf-dev")


# ---------------------------------------------------------------------------
# Pipeline resources
# ---------------------------------------------------------------------------


def _create_data_source(api: SearchApiClient, index_name: str, blob_prefix: str) -> None:
    """Create or update the blob storage data source."""
    storage_url = get_env("AZURE_STORAGE_ACCOUNT_URL").rstrip("/")
    container = os.environ.get("AZURE_STORAGE_CONTAINER", "documents")
    ds_name = f"{index_name}-datasource"

    account_name = _extract_storage_account_name(storage_url)
    resource_id = (
        f"/subscriptions/{_get_subscription_id()}"
        f"/resourceGroups/{_get_resource_group()}"
        f"/providers/Microsoft.Storage/storageAccounts/{account_name}"
    )

    body = {
        "name": ds_name,
        "type": "azureblob",
        "credentials": {
            "connectionString": f"ResourceId={resource_id};",
        },
        "container": {
            "name": container,
            "query": blob_prefix.rstrip("/"),
        },
        "dataDeletionDetectionPolicy": {
            "@odata.type": ("#Microsoft.Azure.Search.NativeBlobSoftDeleteDeletionDetectionPolicy"),
        },
    }

    resp = api.request("PUT", f"datasources/{ds_name}", body)
    api.check_response(resp, f"Data source '{ds_name}'")


def _create_index(api: SearchApiClient, index_name: str) -> None:
    """Create or update the search index schema.

    Field names are aligned with the primary ``surf-index`` schema so the
    API can query both indexes using the same code.
    """
    openai_endpoint = get_env("AZURE_OPENAI_ENDPOINT").rstrip("/")
    embedding_deployment = os.environ.get(
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", _EMBEDDING_DEPLOYMENT_DEFAULT
    )

    body = {
        "name": index_name,
        "fields": [
            {
                "name": "chunk_id",
                "type": "Edm.String",
                "key": True,
                "filterable": True,
                "analyzer": "keyword",
            },
            {
                "name": "parent_id",
                "type": "Edm.String",
                "filterable": True,
            },
            {
                "name": "document_id",
                "type": "Edm.String",
                "filterable": True,
            },
            {
                "name": "title",
                "type": "Edm.String",
                "searchable": True,
                "retrievable": True,
            },
            {
                "name": "content",
                "type": "Edm.String",
                "searchable": True,
                "retrievable": True,
            },
            {
                "name": "content_vector",
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "retrievable": True,
                "dimensions": 3072,
                "vectorSearchProfile": "content-vector-profile",
            },
            {
                "name": "source_url",
                "type": "Edm.String",
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "source_type",
                "type": "Edm.String",
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "domain",
                "type": "Edm.String",
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "document_type",
                "type": "Edm.String",
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "section_heading",
                "type": "Edm.String",
                "searchable": True,
                "retrievable": True,
            },
            {
                "name": "chunk_index",
                "type": "Edm.Int32",
                "retrievable": True,
            },
        ],
        "vectorSearch": {
            "algorithms": [{"name": "hnsw-algo", "kind": "hnsw"}],
            "profiles": [
                {
                    "name": "content-vector-profile",
                    "algorithm": "hnsw-algo",
                    "vectorizer": "openai-vectorizer",
                },
            ],
            "vectorizers": [
                {
                    "name": "openai-vectorizer",
                    "kind": "azureOpenAI",
                    "azureOpenAIParameters": {
                        "resourceUri": openai_endpoint,
                        "deploymentId": embedding_deployment,
                        "modelName": _EMBEDDING_MODEL,
                    },
                },
            ],
        },
    }

    resp = api.request("PUT", f"indexes/{index_name}", body)
    api.check_response(resp, f"Index '{index_name}'")


def _create_skillset(api: SearchApiClient, index_name: str) -> None:
    """Create or update the skillset with text splitting and embedding."""
    skillset_name = f"{index_name}-skillset"
    openai_endpoint = get_env("AZURE_OPENAI_ENDPOINT").rstrip("/")
    embedding_deployment = os.environ.get(
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", _EMBEDDING_DEPLOYMENT_DEFAULT
    )

    body = {
        "name": skillset_name,
        "skills": [
            {
                "@odata.type": "#Microsoft.Skills.Text.SplitSkill",
                "name": "text-split",
                "description": "Split documents into chunks",
                "textSplitMode": "pages",
                "maximumPageLength": 2000,
                "pageOverlapLength": 200,
                "context": "/document",
                "inputs": [
                    {"name": "text", "source": "/document/content"},
                ],
                "outputs": [
                    {"name": "textItems", "targetName": "pages"},
                ],
            },
            {
                "@odata.type": ("#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill"),
                "name": "embedding",
                "description": "Generate embeddings for chunks",
                "resourceUri": openai_endpoint,
                "deploymentId": embedding_deployment,
                "modelName": _EMBEDDING_MODEL,
                "context": "/document/pages/*",
                "inputs": [
                    {"name": "text", "source": "/document/pages/*"},
                ],
                "outputs": [
                    {
                        "name": "embedding",
                        "targetName": "content_vector",
                    },
                ],
            },
        ],
        "indexProjections": {
            "selectors": [
                {
                    "targetIndexName": index_name,
                    "parentKeyFieldName": "parent_id",
                    "sourceContext": "/document/pages/*",
                    "mappings": [
                        {
                            "name": "content",
                            "source": "/document/pages/*",
                        },
                        {
                            "name": "content_vector",
                            "source": "/document/pages/*/content_vector",
                        },
                        {
                            "name": "title",
                            "source": "/document/metadata_storage_name",
                        },
                        {
                            "name": "source_url",
                            "source": "/document/sp_web_url",
                        },
                        {
                            "name": "source_type",
                            "source": "/document/sp_source_type",
                        },
                        {
                            "name": "domain",
                            "source": "/document/sp_domain",
                        },
                        {
                            "name": "document_type",
                            "source": "/document/sp_document_type",
                        },
                    ],
                },
            ],
            "parameters": {
                "projectionMode": "skipIndexingParentDocuments",
            },
        },
    }

    resp = api.request("PUT", f"skillsets/{skillset_name}", body)
    api.check_response(resp, f"Skillset '{skillset_name}'")


def _create_indexer(api: SearchApiClient, index_name: str) -> None:
    """Create or update the indexer."""
    indexer_name = f"{index_name}-indexer"
    ds_name = f"{index_name}-datasource"
    skillset_name = f"{index_name}-skillset"

    body = {
        "name": indexer_name,
        "dataSourceName": ds_name,
        "targetIndexName": index_name,
        "skillsetName": skillset_name,
        "parameters": {
            "configuration": {
                "dataToExtract": "contentAndMetadata",
                "parsingMode": "default",
                "indexedFileNameExtensions": _INDEXED_EXTENSIONS,
            },
        },
        "fieldMappings": [
            {
                "sourceFieldName": "metadata_storage_name",
                "targetFieldName": "title",
            },
        ],
        "outputFieldMappings": [],
        "schedule": {"interval": os.environ.get("INDEXER_SCHEDULE_INTERVAL", "PT1H")},
    }

    resp = api.request("PUT", f"indexers/{indexer_name}", body)
    api.check_response(resp, f"Indexer '{indexer_name}'")


def _teardown(api: SearchApiClient, index_name: str) -> None:
    """Delete all pipeline resources."""
    for resource_type, name in [
        ("indexers", f"{index_name}-indexer"),
        ("skillsets", f"{index_name}-skillset"),
        ("datasources", f"{index_name}-datasource"),
        ("indexes", index_name),
    ]:
        resp = api.request("DELETE", f"{resource_type}/{name}")
        if resp.status_code in (200, 204, 404):
            status = "deleted" if resp.status_code != 404 else "not found"
            click.echo(f"  {resource_type}/{name}: {status}")
        else:
            click.echo(
                f"  FAILED to delete {resource_type}/{name}: {resp.status_code}",
                err=True,
            )


@click.command()
@click.option(
    "--teardown",
    is_flag=True,
    help="Delete all indexer pipeline resources",
)
@click.option(
    "--index-name",
    default=None,
    help="Index name (default: AZURE_SEARCH_SHAREPOINT_INDEX or surf-sharepoint-index)",
)
def main(teardown: bool, index_name: str | None) -> None:
    """Create or teardown the SharePoint indexer pipeline."""
    resolved_name = index_name or os.environ.get(
        "AZURE_SEARCH_SHAREPOINT_INDEX", "surf-sharepoint-index"
    )
    blob_prefix = os.environ.get("AZURE_STORAGE_BLOB_PREFIX", "sharepoint/")

    click.echo(f"Index: {resolved_name}")

    with httpx.Client(timeout=30) as client:
        api = SearchApiClient(client)
        if teardown:
            click.echo("Tearing down indexer pipeline...")
            _teardown(api, resolved_name)
        else:
            click.echo("Setting up indexer pipeline...")
            _create_data_source(api, resolved_name, blob_prefix)
            _create_index(api, resolved_name)
            _create_skillset(api, resolved_name)
            _create_indexer(api, resolved_name)
            click.echo("\nDone! Run the indexer with: uv run python scripts/run_indexer.py")


if __name__ == "__main__":
    main()
