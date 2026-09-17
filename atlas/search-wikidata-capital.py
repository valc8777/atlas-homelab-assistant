#!/usr/bin/env python3

import argparse
import json
import sys

from api_adapter_common import cache_metadata, fetch_json_cached


SEARCH_URL = "https://www.wikidata.org/w/api.php"
ENTITY_DATA_PREFIX = "https://www.wikidata.org/wiki/Special:EntityData"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
USER_AGENT = "homelab-ai-wikidata/1.0"
CAPITAL_PROPERTY = "P36"


def search_entity(query: str) -> dict:
    return fetch_json_cached(
        namespace="wikidata-search",
        url=SEARCH_URL,
        params={
            "action": "wbsearchentities",
            "search": query,
            "language": "en",
            "format": "json",
            "limit": 1,
            "type": "item",
        },
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        ttl_seconds=CACHE_TTL_SECONDS,
    )


def fetch_entity(entity_id: str) -> dict:
    return fetch_json_cached(
        namespace="wikidata-entity",
        url=f"{ENTITY_DATA_PREFIX}/{entity_id}.json",
        params={},
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        ttl_seconds=CACHE_TTL_SECONDS,
    )


def extract_label(entity_payload: dict, entity_id: str) -> str | None:
    entity = (entity_payload.get("entities") or {}).get(entity_id) or {}
    labels = entity.get("labels") or {}
    english = labels.get("en") or {}
    return english.get("value")


def select_current_claim(claims: list[dict]) -> dict | None:
    usable = [
        claim for claim in claims
        if ((claim.get("mainsnak") or {}).get("snaktype") in {None, "value"})
        and not ((claim.get("qualifiers") or {}).get("P582"))
    ]
    preferred = [claim for claim in usable if claim.get("rank") == "preferred"]
    return (preferred or usable or claims or [None])[0]


def summarize(country: str) -> dict:
    search_payload = search_entity(country)
    results = search_payload.get("search") or []
    top = results[0] if results else {}
    country_id = top.get("id")
    country_payload = fetch_entity(country_id) if country_id else {}

    capital_id = None
    if country_id:
        entity = (country_payload.get("entities") or {}).get(country_id) or {}
        claims = entity.get("claims") or {}
        capital_claims = claims.get(CAPITAL_PROPERTY) or []
        if capital_claims:
            capital_claim = select_current_claim(capital_claims) or {}
            capital_id = (
                ((capital_claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
            ).get("id")

    capital_payload = fetch_entity(capital_id) if capital_id else {}
    cache = cache_metadata(capital_payload or country_payload or search_payload)

    return {
        "query": country,
        "country": {
            "id": country_id,
            "label": extract_label(country_payload, country_id) if country_id else top.get("label"),
            "description": top.get("description"),
        },
        "capital": {
            "id": capital_id,
            "label": extract_label(capital_payload, capital_id) if capital_id else None,
            "url": f"https://www.wikidata.org/wiki/{capital_id}" if capital_id else None,
        },
        "source": "Wikidata",
        "source_url": "https://www.wikidata.org/wiki/Help:Data_access",
        "provenance": {
            "cache_hit": cache["hit"],
            "cache_age_seconds": cache["age_seconds"],
            "cache_ttl_seconds": cache["ttl_seconds"],
            "cache_path": cache["path"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("country", nargs="+", help="Country name to resolve capital for")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON summary")
    args = parser.parse_args()

    country = " ".join(args.country)
    summary = summarize(country)

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    capital = summary["capital"]["label"]
    if not capital:
        print(f"No capital resolved for: {country}")
        print("Source: Wikidata")
        return

    print(f"Capital of {summary['country']['label'] or country}: {capital}")
    if summary["capital"]["url"]:
        print(f"URL: {summary['capital']['url']}")
    print(
        f"Source: Wikidata | Cache hit: {summary['provenance']['cache_hit']} | "
        f"Cache age: {summary['provenance']['cache_age_seconds']}s"
    )


if __name__ == "__main__":
    main()
