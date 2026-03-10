"""Quick local test of RAG search against the SharePoint index.

Usage:
    cd ingestion && source .venv/bin/activate
    python scripts/test_rag_query.py "cyber security policy"
    python scripts/test_rag_query.py "what is the complaints process"
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")


async def main(query: str) -> None:
    from azure.identity.aio import DefaultAzureCredential
    from azure.search.documents.aio import SearchClient
    from azure.search.documents.models import VectorizedQuery
    from openai import AsyncAzureOpenAI

    credential = DefaultAzureCredential()

    search_endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    index_name = os.environ.get("AZURE_SEARCH_SHAREPOINT_INDEX", "surf-sharepoint-index")
    openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]

    # Get a token for Azure OpenAI
    token = await credential.get_token("https://cognitiveservices.azure.com/.default")

    openai_client = AsyncAzureOpenAI(
        azure_endpoint=openai_endpoint,
        azure_ad_token=token.token,
        api_version="2024-02-01",
    )

    # Embed the query
    print(f"Query: {query!r}")
    print("Generating embedding...")
    embed_resp = await openai_client.embeddings.create(
        input=[query], model="text-embedding-3-large"
    )
    vector = embed_resp.data[0].embedding
    print(f"  Embedding: {len(vector)} dimensions")

    # Hybrid search
    search_client = SearchClient(
        endpoint=search_endpoint,
        index_name=index_name,
        credential=credential,
    )

    print(f"\nSearching index '{index_name}' (hybrid: keyword + vector)...\n")

    results = await search_client.search(  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
        search_text=query,
        top=5,
        vector_queries=[
            VectorizedQuery(
                vector=vector,
                k_nearest_neighbors=5,
                fields="content_vector",
            )
        ],
        select=["chunk_id", "title", "content", "domain", "document_type"],
    )

    count = 0
    async for raw_doc in results:  # pyright: ignore[reportUnknownVariableType]
        count += 1
        doc: dict[str, object] = dict(raw_doc)  # pyright: ignore[reportUnknownArgumentType]
        title = str(doc.get("title", "(no title)"))
        score = float(doc.get("@search.score", 0.0) or 0.0)  # pyright: ignore[reportArgumentType]
        content = str(doc.get("content", ""))[:200]
        domain = str(doc.get("domain", "?"))
        doc_type = str(doc.get("document_type", "?"))
        print(f"--- Result {count} (score: {score:.4f}) ---")
        print(f"  Title: {title}")
        print(f"  Domain: {domain} | Type: {doc_type}")
        print(f"  Content: {content}...")
        print()

    if count == 0:
        print("No results found.")
    else:
        print(f"Total: {count} results")

    await search_client.close()
    await openai_client.close()
    await credential.close()


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "cyber security policy"
    asyncio.run(main(q))
