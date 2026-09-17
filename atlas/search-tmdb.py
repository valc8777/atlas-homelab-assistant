#!/usr/bin/env python3

import argparse
import json
import os
import sys
import urllib.parse

from api_adapter_common import cache_metadata, fetch_json_cached


SEARCH_BASE_URL = "https://api.themoviedb.org/3/search"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def media_label(result: dict) -> str:
    return result.get("media_type") or "movie"


def title_label(result: dict) -> str:
    return result.get("title") or result.get("name") or "Unknown"


def release_label(result: dict) -> str | None:
    return result.get("release_date") or result.get("first_air_date")


def result_url(result: dict) -> str | None:
    media_type = media_label(result)
    if media_type not in {"movie", "tv"}:
        return None
    item_id = result.get("id")
    if not item_id:
        return None
    return f"https://www.themoviedb.org/{urllib.parse.quote(media_type)}/{item_id}"


def search_tmdb(query: str, search_type: str, year: int | None) -> dict:
    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        raise SystemExit("TMDB_API_KEY is required")
    params = {
        "api_key": api_key,
        "query": query,
        "include_adult": "false",
        "language": "en-US",
        "page": 1,
    }
    if year is not None:
        params["year"] = year
    return fetch_json_cached(
        namespace=f"tmdb-{search_type}",
        url=f"{SEARCH_BASE_URL}/{search_type}",
        params=params,
        headers={"Accept": "application/json", "User-Agent": "homelab-ai-tmdb/1.0"},
        ttl_seconds=CACHE_TTL_SECONDS,
    )


def summarize(query: str, search_type: str, payload: dict) -> dict:
    results = payload.get("results") or []
    top = results[0] if results else {}
    cache = cache_metadata(payload)
    return {
        "query": query,
        "search_type": search_type,
        "result_count": payload.get("total_results", len(results)),
        "top_result": {
            "id": top.get("id"),
            "media_type": media_label(top) if top else None,
            "title": title_label(top) if top else None,
            "original_title": top.get("original_title") or top.get("original_name"),
            "release_date": release_label(top) if top else None,
            "overview": top.get("overview"),
            "original_language": top.get("original_language"),
            "popularity": top.get("popularity"),
            "vote_average": top.get("vote_average"),
            "vote_count": top.get("vote_count"),
            "adult": top.get("adult"),
            "tmdb_url": result_url(top),
        },
        "source": "TMDb",
        "source_url": "https://developer.themoviedb.org/docs/getting-started",
        "provenance": {
            "cache_hit": cache["hit"],
            "cache_age_seconds": cache["age_seconds"],
            "cache_ttl_seconds": cache["ttl_seconds"],
            "cache_path": cache["path"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="+", help="Movie or TV title to search")
    parser.add_argument("--type", choices=["movie", "tv", "multi"], default="multi")
    parser.add_argument("--year", type=int, help="Optional release year filter")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON summary")
    args = parser.parse_args()

    query = " ".join(args.query)
    summary = summarize(query, args.type, search_tmdb(query, args.type, args.year))

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    top = summary["top_result"]
    if not top["id"]:
        print(f"No TMDb result found for: {query}")
        print("Source: TMDb")
        return

    print(f"Top match: {top['title']} ({top['media_type']})")
    print(f"Release: {top['release_date']} | TMDb ID: {top['id']}")
    print(f"Language: {top['original_language']} | Rating: {top['vote_average']} ({top['vote_count']} votes)")
    if top.get("overview"):
        print(f"Overview: {top['overview']}")
    if top.get("tmdb_url"):
        print(f"URL: {top['tmdb_url']}")
    print(
        f"Source: TMDb | Cache hit: {summary['provenance']['cache_hit']} | "
        f"Cache age: {summary['provenance']['cache_age_seconds']}s"
    )


if __name__ == "__main__":
    main()
