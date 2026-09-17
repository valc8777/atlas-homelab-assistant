#!/usr/bin/env python3

"""Read-only, allowlisted Home Assistant state adapter for ATLAS."""

from __future__ import annotations

import ipaddress
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone


ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
SAFE_ATTRIBUTES = {
    "binary_sensor": {"friendly_name", "device_class"},
    "climate": {"friendly_name", "current_temperature", "temperature", "hvac_action"},
    # rgb_color/hs_color/xy_color/effect added 2026-09-05 alongside the color/
    # effect control extension -- needed so read-after-write verification for
    # a color/effect change can actually see the result; see sanitize_state()'s
    # value-type check below for the matching list-of-numbers allowance.
    "light": {"friendly_name", "brightness", "color_temp_kelvin", "rgb_color", "hs_color", "xy_color", "effect", "color_mode"},
    "lock": {"friendly_name"},
    "sensor": {"friendly_name", "device_class", "unit_of_measurement"},
}


def validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Home Assistant URL must use http or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Home Assistant URL must not contain credentials, query, or fragment")
    host = parsed.hostname.lower()
    private_host = host in {"localhost", "homeassistant.local"} or host.endswith(".ts.net")
    try:
        private_host = private_host or ipaddress.ip_address(host).is_private
    except ValueError:
        pass
    if not private_host:
        raise ValueError("Home Assistant must use a private-network endpoint")
    return value.rstrip("/")


def parse_allowlist(raw: str) -> set[str]:
    if not raw.strip():
        return set()
    value = json.loads(raw)
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("Home Assistant entity allowlist must be a list of at most 100 IDs")
    entities = {str(item).strip() for item in value}
    if not entities or any(not ENTITY_RE.fullmatch(item) for item in entities):
        raise ValueError("Home Assistant allowlist contains an invalid entity ID")
    return entities


def sanitize_state(payload: dict, allowlist: set[str], fetched_at: str | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Home Assistant state must be an object")
    entity_id = str(payload.get("entity_id") or "")
    if entity_id not in allowlist:
        raise PermissionError("Home Assistant entity is not allowlisted")
    domain = entity_id.split(".", 1)[0]
    if domain not in SAFE_ATTRIBUTES:
        raise PermissionError("Home Assistant entity domain is not approved")
    attributes = payload.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise ValueError("Home Assistant attributes must be an object")

    def _is_safe_value(value) -> bool:
        if isinstance(value, (str, int, float, bool, type(None))):
            return True
        # rgb_color/xy_color are short lists of plain numbers (e.g. [255, 0, 0]);
        # allow only that narrow shape, not arbitrary nested structures.
        return isinstance(value, (list, tuple)) and 0 < len(value) <= 4 and all(isinstance(item, (int, float)) for item in value)

    safe_attributes = {
        key: value
        for key, value in attributes.items()
        if key in SAFE_ATTRIBUTES[domain] and _is_safe_value(value)
    }
    return {
        "entity_id": entity_id,
        "domain": domain,
        "state": str(payload.get("state") or "unknown"),
        "attributes": safe_attributes,
        "source_updated_at": payload.get("last_updated"),
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "home_assistant",
        "read_only": True,
    }


def fetch_state(
    base_url: str,
    token: str,
    entity_id: str,
    allowlist: set[str],
    timeout: int = 10,
    opener=urllib.request.urlopen,
) -> dict:
    if not token:
        raise ValueError("Home Assistant token is not configured")
    if entity_id not in allowlist:
        raise PermissionError("Home Assistant entity is not allowlisted")
    url = f"{validate_base_url(base_url)}/api/states/{urllib.parse.quote(entity_id, safe='._')}"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with opener(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return sanitize_state(payload, allowlist)


# --- Scoped control exception -----------------------------------------------
#
# Everything above this line is the original read-only adapter: GET-only,
# up-to-100-entity allowlist, five approved domains. This function is a
# deliberate, narrow departure from that stance -- owner-approved control of
# exactly one light entity, on/off only. See ATLAS_HOME_ASSISTANT_READONLY.md
# ("Scoped Control Exception") before extending this to any other entity,
# domain, or action. `allowed_entity` is a single hardcoded entity, not a
# general allowlist -- deliberately narrower than the read path above.
LIGHT_ACTIONS = {"turn_on", "turn_off"}

# Extended 2026-09-05 after discovering this specific bulb's real hardware
# supports full RGB color and built-in dynamic effects -- cync-lan's own
# device-type table was simply missing this bulb's type code (155, sitting
# between two other RGB-capable codes, 154 and 156), causing it to report as
# brightness-only until corrected directly in cync_mesh.yaml. KNOWN_EFFECTS
# is the exact effect_list Home Assistant reported for this device once that
# was fixed -- not a general Cync effect list, just what this bulb offers.
KNOWN_EFFECTS = {
    "candle", "cyber", "rainbow", "fireworks", "volcanic", "aurora",
    "happy_holidays", "red_white_blue", "vegas", "party_time",
}

# A modest, hardcoded set of common color names -> RGB. Deliberately not
# trying to parse arbitrary color descriptions -- an unrecognized color name
# should fail closed (ask for clarification), not guess.
NAMED_COLORS = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "orange": (255, 140, 0),
    "purple": (128, 0, 128),
    "pink": (255, 105, 180),
    "white": (255, 255, 255),
    "cyan": (0, 255, 255),
    "teal": (0, 128, 128),
    "magenta": (255, 0, 255),
    "warm white": (255, 223, 186),
}


def control_light(
    base_url: str,
    token: str,
    entity_id: str,
    action: str,
    allowed_entity: str,
    service_data: dict | None = None,
    timeout: int = 10,
    opener=urllib.request.urlopen,
    verify_attempts: int = 7,
    verify_delay_seconds: float = 1.5,
    sleep_fn=time.sleep,
) -> dict:
    if not token:
        raise ValueError("Home Assistant token is not configured")
    if not allowed_entity:
        raise ValueError("Home Assistant controllable entity is not configured")
    if entity_id != allowed_entity:
        raise PermissionError("Home Assistant entity is not the approved controllable entity")
    if action not in LIGHT_ACTIONS:
        raise ValueError("Home Assistant light action must be turn_on or turn_off")

    service_data = dict(service_data or {})
    unexpected_keys = set(service_data) - {"rgb_color", "effect"}
    if unexpected_keys:
        raise ValueError(f"Unsupported light control fields: {sorted(unexpected_keys)}")
    if service_data and action != "turn_on":
        raise ValueError("Color/effect data is only valid together with turn_on")
    if "rgb_color" in service_data:
        rgb = service_data["rgb_color"]
        if not (
            isinstance(rgb, (list, tuple))
            and len(rgb) == 3
            and all(isinstance(component, int) and 0 <= component <= 255 for component in rgb)
        ):
            raise ValueError("rgb_color must be exactly three integers between 0 and 255")
        service_data["rgb_color"] = list(rgb)
    if "effect" in service_data and service_data["effect"] not in KNOWN_EFFECTS:
        raise ValueError(f"Unknown light effect: {service_data['effect']!r}")

    url = f"{validate_base_url(base_url)}/api/services/light/{action}"
    body = json.dumps({"entity_id": entity_id, **service_data}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with opener(request, timeout=timeout) as response:
        response.read()  # drain; the service-call response isn't the state we verify against
    # Read-after-write verification, scoped to just this one entity -- independent
    # of whatever the general read-path allowlist currently contains.
    #
    # This is a real physical device behind an MQTT bridge (cync-lan), not a
    # local database row: HA's service call returning success only means the
    # command was dispatched, not that the bulb has finished acting on it and
    # reporting its new state back up through cync-lan -> MQTT -> HA. A single
    # immediate read genuinely raced this and reported a false mismatch for a
    # command that had, in fact, worked. Live-measured round-trip latency for
    # this specific device varied from under a second up to ~20 seconds
    # (cync-lan's own logs showed a ~20s gap between receiving the MQTT
    # command and the bulb's own status report coming back) -- the ~10s
    # default here (7 attempts, 1.5s apart) is a deliberate speed/certainty
    # tradeoff, not a guarantee: it catches most real responses without
    # making a simple "turn on the light" request feel broken, but a
    # genuinely slow round-trip will still resolve to the honest "sent, not
    # confirmed yet" answer in run_home_light() rather than a false claim of
    # success either way.
    expected_state = "on" if action == "turn_on" else "off"
    state = None
    for attempt in range(verify_attempts):
        if attempt > 0:
            sleep_fn(verify_delay_seconds)
        state = fetch_state(base_url, token, entity_id, {entity_id}, timeout=timeout, opener=opener)
        mismatch = []
        if state.get("state") != expected_state:
            mismatch.append("state_mismatch")
        if "rgb_color" in service_data:
            actual_rgb = tuple(state.get("attributes", {}).get("rgb_color") or ())
            if actual_rgb != tuple(service_data["rgb_color"]):
                mismatch.append("color_mismatch")
        if "effect" in service_data and state.get("attributes", {}).get("effect") != service_data["effect"]:
            mismatch.append("effect_mismatch")
        if not mismatch:
            return {"ok": True, "state": state, "mismatch": []}
    return {"ok": False, "state": state, "mismatch": mismatch}
