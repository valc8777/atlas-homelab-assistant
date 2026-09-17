#!/usr/bin/env python3

import argparse
import json
import sys

from api_adapter_common import cache_metadata, fetch_json_cached


SEARCH_URL = "https://openlibrary.org/search.json"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def search_openlibrary(title: str, author: str | None) -> dict:
    params = {"title": title, "limit": 5}
    if author:
        params["author"] = author
    return fetch_json_cached(
        namespace="openlibrary-search",
        url=SEARCH_URL,
        params=params,
        headers={"Accept": "application/json", "User-Agent": "homelab-ai-openlibrary/1.0"},
        ttl_seconds=CACHE_TTL_SECONDS,
    )


def summarize(title: str, author: str | None, payload: dict) -> dict:
    docs = payload.get("docs") or []
    top = docs[0] if docs else {}
    cache = cache_metadata(payload)
    key = top.get("key")
    return {
        "query": {"title": title, "author": author},
        "result_count": payload.get("numFound", len(docs)),
        "top_result": {
            "key": key,
            "title": top.get("title"),
            "author_name": (top.get("author_name") or [None])[0],
            "first_publish_year": top.get("first_publish_year"),
            "edition_count": top.get("edition_count"),
            "language": (top.get("language") or [None])[0],
            "isbn": (top.get("isbn") or [None])[0],
            "cover_id": top.get("cover_i"),
            "openlibrary_url": f"https://openlibrary.org{key}" if key else None,
        },
        "source": "Open Library",
        "source_url": "https://openlibrary.org/developers/api",
        "provenance": {
            "cache_hit": cache["hit"],
            "cache_age_seconds": cache["age_seconds"],
            "cache_ttl_seconds": cache["ttl_seconds"],
            "cache_path": cache["path"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("title", nargs="+", help="Book title to search")
    parser.add_argument("--author", help="Optional author filter")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON summary")
    args = parser.parse_args()

    title = " ".join(args.title)
    summary = summarize(title, args.author, search_openlibrary(title, args.author))

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    top = summary["top_result"]
    if not top["key"]:
        print(f"No Open Library result found for: {title}")
        print("Source: Open Library")
        return

    print(f"Top match: {top['title']}")
    print(f"Author: {top['author_name']} | First published: {top['first_publish_year']}")
    print(f"Editions: {top['edition_count']} | ISBN: {top['isbn']}")
    if top.get("openlibrary_url"):
        print(f"URL: {top['openlibrary_url']}")
    print(
        f"Source: Open Library | Cache hit: {summary['provenance']['cache_hit']} | "
        f"Cache age: {summary['provenance']['cache_age_seconds']}s"
    )


if __name__ == "__main__":
    main()
