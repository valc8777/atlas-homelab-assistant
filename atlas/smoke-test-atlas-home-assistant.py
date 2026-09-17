#!/usr/bin/env python3

import json

import atlas_home_assistant as ha


class Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def main() -> None:
    allowlist = ha.parse_allowlist('["light.office", "climate.home", "lock.front_door"]')
    fixture = {
        "entity_id": "light.office",
        "state": "on",
        "last_updated": "2026-08-29T18:00:00+00:00",
        "attributes": {
            "friendly_name": "Office light",
            "brightness": 128,
            "effect_list": ["secret", "internal"],
        },
    }
    sanitized = ha.sanitize_state(fixture, allowlist, "2026-08-29T18:01:00+00:00")
    assert sanitized["state"] == "on"
    assert sanitized["read_only"] is True
    assert sanitized["attributes"] == {"friendly_name": "Office light", "brightness": 128}
    assert ha.validate_base_url("http://192.168.1.50:8123")
    assert ha.validate_base_url("https://home-assistant.example.ts.net")
    for unsafe_url in ("https://example.com", "file:///tmp/ha", "https://user:pass@192.168.1.50"):
        try:
            ha.validate_base_url(unsafe_url)
        except ValueError:
            pass
        else:
            raise AssertionError("non-private or credential-bearing Home Assistant URL must be rejected")
    try:
        ha.sanitize_state({**fixture, "entity_id": "light.bedroom"}, allowlist)
    except PermissionError:
        pass
    else:
        raise AssertionError("non-allowlisted entity must be rejected")

    seen = {}

    def opener(request, timeout):
        seen["method"] = request.method
        seen["url"] = request.full_url
        seen["authorization"] = request.headers.get("Authorization")
        return Response(fixture)

    fetched = ha.fetch_state(
        "http://192.168.1.50:8123",
        "synthetic-token",
        "light.office",
        allowlist,
        opener=opener,
    )
    assert fetched["entity_id"] == "light.office"
    assert seen["method"] == "GET"
    assert seen["url"].endswith("/api/states/light.office")
    assert seen["authorization"] == "Bearer synthetic-token"
    assert not hasattr(ha, "call_service")

    # -- Scoped control exception: control_light() -----------------------
    # Deliberately testing against a narrow, purpose-built function, not a
    # generic call_service escape hatch (still asserted absent above).

    controllable_entity = "light.bedside_table_lamp"

    # Wrong entity must be rejected before any HTTP call happens.
    def opener_should_not_be_called(request, timeout):
        raise AssertionError("HTTP call must not happen for a non-approved entity")

    try:
        ha.control_light(
            "http://192.168.1.50:8123",
            "synthetic-token",
            "light.office",
            "turn_on",
            controllable_entity,
            opener=opener_should_not_be_called,
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("non-approved entity must be rejected for control")

    # Invalid action must be rejected before any HTTP call happens.
    try:
        ha.control_light(
            "http://192.168.1.50:8123",
            "synthetic-token",
            controllable_entity,
            "set_brightness",
            controllable_entity,
            opener=opener_should_not_be_called,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("action other than turn_on/turn_off must be rejected")

    # Happy path: correct entity + action -> POST service call, then a GET
    # read-after-write check that confirms the light actually changed state.
    control_calls = []

    def happy_opener(request, timeout):
        control_calls.append({
            "method": request.method,
            "url": request.full_url,
            "authorization": request.headers.get("Authorization"),
            "body": request.data,
        })
        if request.method == "POST":
            return Response({})  # HA's service-call response isn't inspected
        return Response({
            "entity_id": controllable_entity,
            "state": "on",
            "last_updated": "2026-09-05T22:00:00+00:00",
            "attributes": {"friendly_name": "Bedside Table Lamp", "brightness": 200},
        })

    outcome = ha.control_light(
        "http://192.168.1.50:8123",
        "synthetic-token",
        controllable_entity,
        "turn_on",
        controllable_entity,
        opener=happy_opener,
    )
    assert outcome["ok"] is True
    assert outcome["mismatch"] == []
    assert outcome["state"]["state"] == "on"
    post_call = next(call for call in control_calls if call["method"] == "POST")
    assert post_call["url"].endswith("/api/services/light/turn_on")
    assert post_call["authorization"] == "Bearer synthetic-token"
    assert json.loads(post_call["body"]) == {"entity_id": controllable_entity}
    get_call = next(call for call in control_calls if call["method"] == "GET")
    assert get_call["url"].endswith(f"/api/states/{controllable_entity}")

    # Read-after-write mismatch: HA's service call "succeeds" (200) but the
    # light never reports the expected state even after retrying (a real
    # device has propagation latency -- see the multi-attempt poll test
    # below for that path; this is the "genuinely never catches up" case).
    # Must be reported as a real failure, not silently swallowed as success.
    def mismatch_opener(request, timeout):
        if request.method == "POST":
            return Response({})
        return Response({
            "entity_id": controllable_entity,
            "state": "off",  # requested turn_on, but HA still reports off
            "last_updated": "2026-09-05T22:00:00+00:00",
            "attributes": {"friendly_name": "Bedside Table Lamp"},
        })

    # verify_attempts/verify_delay_seconds passed explicitly here so these
    # tests stay stable and fast regardless of future production-default
    # tuning (see the tradeoff note on control_light() itself).
    sleeps = []
    mismatch_outcome = ha.control_light(
        "http://192.168.1.50:8123",
        "synthetic-token",
        controllable_entity,
        "turn_on",
        controllable_entity,
        opener=mismatch_opener,
        verify_attempts=4,
        verify_delay_seconds=0.01,
        sleep_fn=sleeps.append,
    )
    assert mismatch_outcome["ok"] is False
    assert mismatch_outcome["mismatch"] == ["state_mismatch"]
    assert len(sleeps) == 3  # 4 attempts total, sleeps between each but not before the first

    # Real-world case this retry loop exists for: the first read(s) catch a
    # real device's genuine propagation latency (stale state), a later read
    # catches the real change. Must resolve to ok=True, not give up early.
    late_state_calls = {"gets": 0}

    def eventually_consistent_opener(request, timeout):
        if request.method == "POST":
            return Response({})
        late_state_calls["gets"] += 1
        # First two reads still show the pre-command state; third read (the
        # third fetch_state call, second retry) finally reflects the change.
        state = "off" if late_state_calls["gets"] < 3 else "on"
        return Response({
            "entity_id": controllable_entity,
            "state": state,
            "last_updated": "2026-09-05T22:00:00+00:00",
            "attributes": {"friendly_name": "Bedside Table Lamp"},
        })

    eventual_sleeps = []
    eventual_outcome = ha.control_light(
        "http://192.168.1.50:8123",
        "synthetic-token",
        controllable_entity,
        "turn_on",
        controllable_entity,
        opener=eventually_consistent_opener,
        verify_attempts=4,
        verify_delay_seconds=0.01,
        sleep_fn=eventual_sleeps.append,
    )
    assert eventual_outcome["ok"] is True
    assert eventual_outcome["mismatch"] == []
    assert late_state_calls["gets"] == 3  # stopped polling as soon as it matched, not all 4 attempts
    assert len(eventual_sleeps) == 2  # slept before attempt 2 and attempt 3, not after success

    # -- Color/effect extension: found this bulb's real hardware supports ---
    # full RGB + built-in effects after discovering cync-lan's device-type
    # table was simply missing this bulb's type code (155, between two other
    # RGB-capable codes). control_light() now accepts an optional
    # service_data dict for HA's turn_on service (rgb_color/effect), always
    # validated and fail-closed for anything else.

    # Rejects any service_data field other than rgb_color/effect.
    try:
        ha.control_light(
            "http://192.168.1.50:8123", "synthetic-token", controllable_entity, "turn_on", controllable_entity,
            service_data={"brightness": 100}, opener=opener_should_not_be_called,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported light control field must be rejected")

    # Rejects service_data paired with turn_off (color/effect imply turning on).
    try:
        ha.control_light(
            "http://192.168.1.50:8123", "synthetic-token", controllable_entity, "turn_off", controllable_entity,
            service_data={"rgb_color": [255, 0, 0]}, opener=opener_should_not_be_called,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("service_data with turn_off must be rejected")

    # Rejects a malformed rgb_color (wrong length, out-of-range component).
    for bad_rgb in ([255, 0], [255, 0, 999], ["255", 0, 0]):
        try:
            ha.control_light(
                "http://192.168.1.50:8123", "synthetic-token", controllable_entity, "turn_on", controllable_entity,
                service_data={"rgb_color": bad_rgb}, opener=opener_should_not_be_called,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"malformed rgb_color {bad_rgb!r} must be rejected")

    # Rejects an unrecognized effect name -- fail closed, never pass arbitrary
    # text through to the real HA API.
    try:
        ha.control_light(
            "http://192.168.1.50:8123", "synthetic-token", controllable_entity, "turn_on", controllable_entity,
            service_data={"effect": "not_a_real_effect"}, opener=opener_should_not_be_called,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unrecognized effect name must be rejected")

    # Happy path: color. Verifies the POST body includes rgb_color and the
    # read-after-write check compares against the real rgb_color attribute
    # (not just the on/off state).
    color_calls = []

    def color_opener(request, timeout):
        color_calls.append({"method": request.method, "body": request.data})
        if request.method == "POST":
            return Response({})
        return Response({
            "entity_id": controllable_entity,
            "state": "on",
            "last_updated": "2026-09-05T22:00:00+00:00",
            "attributes": {"friendly_name": "Bedside Table Lamp", "rgb_color": [255, 0, 0]},
        })

    color_outcome = ha.control_light(
        "http://192.168.1.50:8123", "synthetic-token", controllable_entity, "turn_on", controllable_entity,
        service_data={"rgb_color": [255, 0, 0]}, opener=color_opener,
        verify_attempts=4, verify_delay_seconds=0.01, sleep_fn=lambda *_: None,
    )
    assert color_outcome["ok"] is True
    assert color_outcome["state"]["attributes"]["rgb_color"] == [255, 0, 0]
    post_body = json.loads(next(call for call in color_calls if call["method"] == "POST")["body"])
    assert post_body == {"entity_id": controllable_entity, "rgb_color": [255, 0, 0]}

    # Color mismatch: HA reports on, but a different color than requested --
    # must be reported as a real failure, not swallowed because state="on" matched.
    def color_mismatch_opener(request, timeout):
        if request.method == "POST":
            return Response({})
        return Response({
            "entity_id": controllable_entity,
            "state": "on",
            "last_updated": "2026-09-05T22:00:00+00:00",
            "attributes": {"friendly_name": "Bedside Table Lamp", "rgb_color": [0, 255, 0]},  # requested red, still green
        })

    color_mismatch = ha.control_light(
        "http://192.168.1.50:8123", "synthetic-token", controllable_entity, "turn_on", controllable_entity,
        service_data={"rgb_color": [255, 0, 0]}, opener=color_mismatch_opener,
        verify_attempts=2, verify_delay_seconds=0.01, sleep_fn=lambda *_: None,
    )
    assert color_mismatch["ok"] is False
    assert "color_mismatch" in color_mismatch["mismatch"]

    # Happy path: effect.
    def effect_opener(request, timeout):
        if request.method == "POST":
            return Response({})
        return Response({
            "entity_id": controllable_entity,
            "state": "on",
            "last_updated": "2026-09-05T22:00:00+00:00",
            "attributes": {"friendly_name": "Bedside Table Lamp", "effect": "rainbow"},
        })

    effect_outcome = ha.control_light(
        "http://192.168.1.50:8123", "synthetic-token", controllable_entity, "turn_on", controllable_entity,
        service_data={"effect": "rainbow"}, opener=effect_opener,
        verify_attempts=4, verify_delay_seconds=0.01, sleep_fn=lambda *_: None,
    )
    assert effect_outcome["ok"] is True
    assert effect_outcome["state"]["attributes"]["effect"] == "rainbow"

    print("ATLAS Home Assistant smoke test passed")


if __name__ == "__main__":
    main()
