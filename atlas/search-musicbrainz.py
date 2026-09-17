#!/usr/bin/env python3

import argparse
import json
import sys
import urllib.parse

from api_adapter_common import cache_metadata, fetch_json_cached


SEARCH_BASE_URL = "https://musicbrainz.org/ws/2"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_USER_AGENT = "homelab-ai-musicbrainz/1.0 (local homelab tool)"


def artist_credit_label(result: dict) -> str | None:
    credits = result.get("artist-credit") or []
    if not credits:
        return None
    names = []
    for item in credits:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
    return "".join(names) if names else None


def result_title(result: dict, entity: str) -> str | None:
    if entity == "artist":
        return result.get("name")
    return result.get("title")


def result_url(result: dict, entity: str) -> str | None:
    item_id = result.get("id")
    if not item_id:
        return None
    return f"https://musicbrainz.org/{urllib.parse.quote(entity)}/{item_id}"


def search_musicbrainz(query: str, entity: str) -> dict:
    payload = fetch_json_cached(
        namespace=f"musicbrainz-{entity}",
        url=f"{SEARCH_BASE_URL}/{entity}",
        params={"query": query, "fmt": "json", "limit": 5},
        headers={"Accept": "application/json", "User-Agent": DEFAULT_USER_AGENT},
        ttl_seconds=CACHE_TTL_SECONDS,
    )
    return payload


def summarize(query: str, entity: str, payload: dict) -> dict:
    key = f"{entity}s"
    results = payload.get(key) or []
    top = results[0] if results else {}
    cache = cache_metadata(payload)
    return {
        "query": query,
        "entity": entity,
        "result_count": payload.get("count", len(results)),
        "top_result": {
            "id": top.get("id"),
            "score": top.get("score"),
            "name": result_title(top, entity),
            "disambiguation": top.get("disambiguation"),
            "artist_credit": artist_credit_label(top),
            "country": top.get("country"),
            "date": top.get("date") or top.get("first-release-date") or top.get("begin-area"),
            "type": top.get("type"),
            "musicbrainz_url": result_url(top, entity),
        },
        "source": "MusicBrainz",
        "source_url": "https://musicbrainz.org/doc/MusicBrainz_API",
        "provenance": {
            "cache_hit": cache["hit"],
            "cache_age_seconds": cache["age_seconds"],
            "cache_ttl_seconds": cache["ttl_seconds"],
            "cache_path": cache["path"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="+", help="Artist, release, or recording search text")
    parser.add_argument(
        "--entity",
        choices=["artist", "release", "recording", "release-group"],
        default="artist",
    )
    parser.add_argument("--json", action="store_true", help="Emit raw JSON summary")
    args = parser.parse_args()

    query = " ".join(args.query)
    summary = summarize(query, args.entity, search_musicbrainz(query, args.entity))

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    top = summary["top_result"]
    if not top["id"]:
        print(f"No MusicBrainz result found for: {query}")
        print("Source: MusicBrainz")
        return

    print(f"Top match: {top['name']} ({args.entity})")
    if top.get("artist_credit"):
        print(f"Artist credit: {top['artist_credit']}")
    print(f"Score: {top['score']} | Type: {top['type']} | Date: {top['date']}")
    if top.get("disambiguation"):
        print(f"Disambiguation: {top['disambiguation']}")
    if top.get("musicbrainz_url"):
        print(f"URL: {top['musicbrainz_url']}")
    print(
        f"Source: MusicBrainz | Cache hit: {summary['provenance']['cache_hit']} | "
        f"Cache age: {summary['provenance']['cache_age_seconds']}s"
    )


if __name__ == "__main__":
    main()
