#!/usr/bin/env python3

import argparse
import json
import os
import pathlib
import sys

from api_adapter_common import cache_metadata, fetch_json_cached


def default_search_url() -> str:
    env_url = os.environ.get("ATLAS_SEARXNG_URL") or os.environ.get("JARVIS_SEARXNG_URL")
    if env_url:
        return env_url
    if pathlib.Path("/.dockerenv").exists():
        return "http://searxng:8080/search"
    return "http://127.0.0.1:8080/search"


SEARCH_URL = default_search_url()
CACHE_TTL_SECONDS = 10 * 60
USER_AGENT = "homelab-ai-searxng/1.0"


def search(query: str) -> dict:
    return fetch_json_cached(
        namespace="searxng-search",
        url=SEARCH_URL,
        params={
            "q": query,
            "format": "json",
            "safesearch": 1,
            "language": "en-US",
        },
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        ttl_seconds=CACHE_TTL_SECONDS,
        timeout=45,
    )


def summarize(query: str) -> dict:
    payload = search(query)
    cache = cache_metadata(payload)
    results = payload.get("results") or []
    answers = payload.get("answers") or []
    infoboxes = payload.get("infoboxes") or []

    normalized_results = []
    for item in results[:8]:
        normalized_results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "content": item.get("content"),
                "engine": item.get("engine"),
                "category": item.get("category"),
            }
        )

    normalized_infoboxes = []
    for item in infoboxes[:3]:
        attributes = item.get("attributes") or []
        normalized_infoboxes.append(
            {
                "title": item.get("infobox") or item.get("title"),
                "content": item.get("content"),
                "url": item.get("urls", [{}])[0].get("url") if item.get("urls") else None,
                "attributes": attributes[:8],
            }
        )

    return {
        "query": query,
        "answers": answers[:5],
        "infoboxes": normalized_infoboxes,
        "results": normalized_results,
        "number_of_results": payload.get("number_of_results"),
        "source": "SearXNG",
        "source_url": SEARCH_URL,
        "provenance": {
            "cache_hit": cache["hit"],
            "cache_age_seconds": cache["age_seconds"],
            "cache_ttl_seconds": cache["ttl_seconds"],
            "cache_path": cache["path"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="+", help="General web search query")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON summary")
    args = parser.parse_args()

    query = " ".join(args.query)
    summary = summarize(query)

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    print(f"Query: {query}")
    if summary["answers"]:
        print(f"Answer: {summary['answers'][0]}")
    for result in summary["results"][:3]:
        print(f"- {result.get('title') or 'Untitled'}")
        if result.get("content"):
            print(f"  {result['content']}")
        if result.get("url"):
            print(f"  {result['url']}")
    print(
        f"Source: SearXNG | Cache hit: {summary['provenance']['cache_hit']} | "
        f"Cache age: {summary['provenance']['cache_age_seconds']}s"
    )


if __name__ == "__main__":
    main()
