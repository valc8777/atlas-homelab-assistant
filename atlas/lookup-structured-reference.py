#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent

DOMAIN_TO_COMMAND = {
    "movie": [str(TOOLS_DIR / "search-tmdb.py"), "--type", "movie"],
    "tv": [str(TOOLS_DIR / "search-tmdb.py"), "--type", "tv"],
    "music": [str(TOOLS_DIR / "search-musicbrainz.py"), "--entity", "artist"],
    "music-album": [str(TOOLS_DIR / "search-musicbrainz.py"), "--entity", "release-group"],
    "music-song": [str(TOOLS_DIR / "search-musicbrainz.py"), "--entity", "recording"],
    "book": [str(TOOLS_DIR / "search-openlibrary.py")],
}


def run_tool(domain: str, query: str, author: str | None) -> dict:
    command = [sys.executable, *DOMAIN_TO_COMMAND[domain]]
    if domain == "book" and author:
        command.extend(["--author", author])
    command.extend(["--json", query])
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("domain", choices=["movie", "tv", "music", "music-album", "music-song", "book"])
    parser.add_argument("query", nargs="+", help="Search text for the selected domain")
    parser.add_argument("--author", help="Optional author filter for book searches")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON summary")
    args = parser.parse_args()

    query = " ".join(args.query)
    payload = run_tool(args.domain, query, args.author)
    summary = {
        "domain": args.domain,
        "query": query,
        "tool": DOMAIN_TO_COMMAND[args.domain][0],
        "result": payload,
    }

    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    print(f"Domain: {args.domain}")
    print(f"Query: {query}")
    top = payload.get("top_result") or {}
    print(f"Top result: {top.get('title') or top.get('name') or top.get('key') or 'None'}")
    print(f"Source: {payload.get('source')}")
    print(f"Tool: {summary['tool']}")


if __name__ == "__main__":
    main()
