#!/usr/bin/env python3

import os

import atlas_nextcloud as nc


def main() -> None:
    try:
        records = nc.list_file_metadata(
            os.environ["ATLAS_NEXTCLOUD_BASE_URL"],
            os.environ["ATLAS_NEXTCLOUD_USERNAME"],
            os.environ["ATLAS_NEXTCLOUD_APP_PASSWORD"],
            "ATLAS",
            ["ATLAS"],
        )
    except Exception as exc:
        print(f"error {type(exc).__name__} {exc}")
        raise
    print(f"ok {len(records)}")
    for item in records[:5]:
        print(item.get("href"), item.get("display_name"), item.get("is_collection"))


if __name__ == "__main__":
    main()
