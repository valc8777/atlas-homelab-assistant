#!/usr/bin/env python3

from pathlib import Path


ENV_PATH = Path("/opt/stacks/ai-stack/atlas-shortcuts/atlas-shortcuts.env")
UPDATES = {
    "ATLAS_NEXTCLOUD_CALENDARS": '["Personal (Val)","Contact birthdays (Val)"]',
    "ATLAS_NEXTCLOUD_ADDRESSBOOKS": '["Contacts (Val)"]',
}


def main() -> int:
    text = ENV_PATH.read_text()
    lines = text.splitlines()
    for key, value in UPDATES.items():
        prefix = f"{key}="
        replacement = f"{key}={value}"
        for index, line in enumerate(lines):
            if line.startswith(prefix):
                lines[index] = replacement
                break
        else:
            lines.append(replacement)
    ENV_PATH.write_text("\n".join(lines) + "\n")
    print(f"updated {ENV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
