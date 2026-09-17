#!/usr/bin/env python3
"""Build the ATLAS R2 canonical entity registry from approved sources.

Per ATLAS_RECALL_AND_PARSING_PLAN.md Phase R2: "initial updates are
migrations from reviewed sources" -- this script does not invent aliases.
Every record here traces to an existing approved homelab/ATLAS document,
cited in its `source` field. Re-run this script (not a hand edit) whenever
one of those source documents changes, so the registry never drifts from
the documents it claims to summarize.
"""

import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/Users/valdemort/Projects/homelab/tools/ai")
from atlas_entity_registry import validate_registry  # noqa: E402

ROOT = "/Users/valdemort/Projects/homelab"
SERVICE_LOCATION_INDEX = f"{ROOT}/homelab-service-location-index-2026-08-06.json"
OUT_JSON = f"{ROOT}/atlas-entity-registry-2026-09-11.json"

# Hand-migrated from homelab-codex-handoff-latest.md and
# ATLAS_HOME_ASSISTANT_READONLY.md: Proxmox host/VM identities and the one
# Home Assistant entity ATLAS is allowed to see. These are stable
# infrastructure identities, unlike the service-location index (which is
# regenerated from live discovery), so they are migrated by hand once and
# re-verified against those source documents on future registry rebuilds.
INFRASTRUCTURE_ENTITIES = [
    {
        "entity_id": "proxmox.host.zola-cerebro",
        "entity_type": "host",
        "canonical_name": "zola-cerebro",
        "aliases": ["zola-cerebro", "pve", "proxmox", "proxmox ve", "the proxmox host"],
        "context_scope": "operations",
        "source": "homelab-inventory-2026-06-08.md",
        "status": "active",
    },
    {
        "entity_id": "proxmox.vm.105",
        "entity_type": "virtual_machine",
        "canonical_name": "docker-main",
        "aliases": ["docker-main", "docker main", "VM 105"],
        "context_scope": "operations",
        "source": "homelab-service-location-index-2026-08-06.md",
        "status": "active",
    },
    {
        "entity_id": "proxmox.vm.201",
        "entity_type": "virtual_machine",
        "canonical_name": "ai-stack",
        "aliases": ["ai-stack", "ai stack", "VM 201", "Docker-Heavy", "docker heavy"],
        "context_scope": "operations",
        "source": "homelab-service-location-index-2026-08-06.md",
        "status": "active",
    },
    {
        "entity_id": "proxmox.vm.202",
        "entity_type": "virtual_machine",
        "canonical_name": "Windows",
        "aliases": ["Windows", "the gaming VM", "gaming VM", "Windows guest", "VM 202"],
        "context_scope": "operations",
        "source": "homelab-codex-handoff-latest.md",
        "status": "active",
    },
    {
        "entity_id": "proxmox.vm.205",
        "entity_type": "virtual_machine",
        "canonical_name": "Home Assistant OS",
        "aliases": ["Home Assistant", "HAOS", "VM 205", "the home assistant VM"],
        "context_scope": "operations",
        "source": "homelab-codex-handoff-latest.md",
        "status": "active",
    },
    {
        # Personal scope: this is the one household device ATLAS can read/
        # control (ATLAS_HOME_ASSISTANT_READONLY.md's scoped control
        # exception), not homelab infrastructure -- kept out of the
        # `operations` entities above so an operations-scope question can
        # never resolve a household device by accident. If the owner wants
        # household devices reachable from operations context too, that is
        # a scope-policy decision to make explicitly, not a default here.
        "entity_id": "home_assistant.entity.bedside_table_lamp",
        "entity_type": "home_entity",
        "canonical_name": "Bedside Table Lamp",
        "aliases": ["the bedside lamp", "bedside lamp", "the lamp", "bedside table lamp",
                     "light.bedside_table_lamp"],
        "context_scope": "personal",
        "source": "ATLAS_HOME_ASSISTANT_READONLY.md",
        "status": "active",
    },
]


def _slug(name: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in name).strip("-")


def build_service_entities() -> list[dict]:
    with open(SERVICE_LOCATION_INDEX) as handle:
        index = json.load(handle)
    entities = []
    for service in index["services"]:
        entities.append({
            "entity_id": f"homelab.service.{_slug(service['service'])}",
            "entity_type": "service",
            "canonical_name": service["service"],
            "aliases": list(dict.fromkeys(service.get("aliases") or [])),
            "context_scope": "operations",
            "source": "homelab-service-location-index-2026-08-06.json",
            "status": "active",
        })
    return entities


def build_registry() -> list[dict]:
    updated_at = datetime.now(timezone.utc).isoformat()
    records = build_service_entities() + INFRASTRUCTURE_ENTITIES
    for record in records:
        record["updated_at"] = updated_at
    return validate_registry(records)


def main() -> None:
    registry = build_registry()
    with open(OUT_JSON, "w") as handle:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "entities": registry}, handle, indent=2)
        handle.write("\n")
    print(f"Wrote {len(registry)} validated entities to {OUT_JSON}")


if __name__ == "__main__":
    main()
