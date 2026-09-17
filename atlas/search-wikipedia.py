#!/usr/bin/env python3

import argparse
import json
import sys
import urllib.parse

from api_adapter_common import cache_metadata, fetch_json_cached


SEARCH_URL = "https://en.wikipedia.org/w/api.php"
SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
USER_AGENT = "homelab-ai-wikipedia/1.0"


def search_title(query: str) -> dict:
    return fetch_json_cached(
        namespace="wikipedia-search",
        url=SEARCH_URL,
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 1,
            "format": "json",
        },
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        ttl_seconds=CACHE_TTL_SECONDS,
    )


def fetch_summary(title: str) -> dict:
    return fetch_json_cached(
        namespace="wikipedia-summary",
        url=f"{SUMMARY_URL}/{urllib.parse.quote(title, safe='')}",
        params={},
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        ttl_seconds=CACHE_TTL_SECONDS,
    )


def summarize(query: str) -> dict:
    search_payload = search_title(query)
    search_results = (search_payload.get("query") or {}).get("search") or []
    top = search_results[0] if search_results else {}
    title = top.get("title")
    summary_payload = fetch_summary(title) if title else {}
    cache = cache_metadata(summary_payload or search_payload)
    content_urls = summary_payload.get("content_urls") or {}
    desktop = content_urls.get("desktop") or {}

    return {
        "query": query,
        "top_result": {
            "title": summary_payload.get("title") or title,
            "description": summary_payload.get("description"),
            "extract": summary_payload.get("extract"),
            "extract_html": summary_payload.get("extract_html"),
            "wikidata_id": summary_payload.get("wikibase_item"),
            "page_url": desktop.get("page"),
            "page_type": summary_payload.get("type"),
        },
        "source": "Wikipedia",
        "source_url": "https://www.mediawiki.org/wiki/API:REST_API/en",
        "provenance": {
            "cache_hit": cache["hit"],
            "cache_age_seconds": cache["age_seconds"],
            "cache_ttl_seconds": cache["ttl_seconds"],
            "cache_path": cache["path"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="+", help="General reference query")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON summary")
    args = parser.parse_args()

    query = " ".join(args.query)
    summary = summarize(query)

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    top = summary["top_result"]
    if not top.get("title"):
        print(f"No Wikipedia result found for: {query}")
        print("Source: Wikipedia")
        return

    print(top["title"])
    if top.get("description"):
        print(f"Description: {top['description']}")
    if top.get("extract"):
        print(top["extract"])
    if top.get("page_url"):
        print(f"URL: {top['page_url']}")
    print(
        f"Source: Wikipedia | Cache hit: {summary['provenance']['cache_hit']} | "
        f"Cache age: {summary['provenance']['cache_age_seconds']}s"
    )


if __name__ == "__main__":
    main()
