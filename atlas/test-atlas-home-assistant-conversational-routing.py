#!/usr/bin/env python3

"""Regression tests for the Home Assistant lamp control exception AND the
natural-language read-path fix discovered while verifying it live.

Covers the pieces that smoke-test-atlas-home-assistant.py does not:
- the control classifier (_home_light_requested_action / classify_question
  routing to "home_light") and run_home_light()'s dispatch handler, including
  its feature-flag-off fail-closed behavior
- the read classifier (is_home_lamp_state_question / classify_question
  routing to "home_read") and run_home_lamp_state()'s dispatch handler --
  added after live-testing found the pre-existing home.read capability was
  registered but never actually reachable from a natural-language question
  (only from a dedicated API endpoint requiring an explicit entity_id query
  parameter); this wires the same underlying atlas_home_assistant.fetch_state()
  into conversational routing the same way run_home_light() already does for
  control.

atlas_home_assistant.py's control_light() itself (HTTP call shape, entity/
action rejection, read-after-write verification with retry) is covered
directly in smoke-test-atlas-home-assistant.py.

See ATLAS_HOME_ASSISTANT_READONLY.md ("Scoped Control Exception") for the
full control design: exactly one light entity, on/off only, no confirmation
step (mode="tool", not "action" -- deliberately bypasses the action-authority
confirmation queue used by reminders/tasks/shopping/notes).
"""

import importlib.util
import pathlib
import sys
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "tools" / "ai"
sys.path.insert(0, str(AI_DIR))

RUNNER_SPEC = importlib.util.spec_from_file_location("atlas_runner_home_light", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(RUNNER_SPEC)
assert RUNNER_SPEC.loader
RUNNER_SPEC.loader.exec_module(runner)


def main():
    # -- classifier: positive cases, both noun/on-off orderings -------------
    positives = [
        ("turn on the bedside lamp", "turn_on"),
        ("turn the lamp on", "turn_on"),
        ("please turn off the light", "turn_off"),
        ("turn the bedside table lamp off", "turn_off"),
        ("switch on the light", "turn_on"),
        ("flip the lamp off", "turn_off"),
    ]
    for question, expected in positives:
        got = runner._home_light_requested_action(question)
        assert got == expected, f"{question!r} -> {got!r}, expected {expected!r}"
        assert runner.classify_question(question) == "home_light", f"{question!r} did not classify as home_light"

    # -- classifier: negative cases -- must NOT fire -------------------------
    # "what should I turn on for dinner" caught a real bug in an earlier draft:
    # making the lamp/light noun fully optional let bare "turn on" match
    # anything. The noun is now required (just its position relative to
    # on/off varies), which is what these cases guard against regressing.
    negatives = [
        "what is the speed of light",
        "tell me about lamp technology",
        "what should I turn on for dinner",
        "is the light on",
        "turn on the tv",
        "turn off the music",
        "switch on airplane mode",
        "flip a coin",
        "can you turn on notifications",
        "turn the volume up",
    ]
    for question in negatives:
        got = runner._home_light_requested_action(question)
        assert got is None, f"{question!r} incorrectly matched as {got!r}"
        assert runner.classify_question(question) != "home_light", f"{question!r} incorrectly classified as home_light"

    # -- run_home_light(): feature flag off -> fail closed -------------------
    with mock.patch.dict(
        "os.environ",
        {
            "ATLAS_HOME_ASSISTANT_CONTROL_ENABLED": "false",
            "ATLAS_HOME_ASSISTANT_CONTROLLABLE_ENTITY": "light.bedside_table_lamp",
        },
        clear=False,
    ):
        disabled = runner.run_home_light("turn on the lamp")
    assert disabled["route"] == "home_light"
    assert disabled["result"].get("enabled") is False
    assert "control_light" not in disabled  # never attempted a real call

    # -- run_home_light(): missing entity config -> fail closed --------------
    with mock.patch.dict(
        "os.environ",
        {
            "ATLAS_HOME_ASSISTANT_CONTROL_ENABLED": "true",
            "ATLAS_HOME_ASSISTANT_CONTROLLABLE_ENTITY": "",
        },
        clear=False,
    ):
        no_entity = runner.run_home_light("turn on the lamp")
    assert no_entity["result"].get("enabled") is True  # flag was on...
    assert "Lamp control isn't enabled" in no_entity["answer"]  # ...but still fails closed without an entity

    # -- run_home_light(): unrecognized phrasing -> no HTTP call attempted ---
    with mock.patch.object(runner.atlas_home_assistant, "control_light", side_effect=AssertionError("must not be called")):
        ambiguous = runner.run_home_light("what should I do about the lamp")
    assert ambiguous["result"] == {"question": "what should I do about the lamp"}

    # -- run_home_light(): happy path, mocked control_light -------------------
    with mock.patch.dict(
        "os.environ",
        {
            "ATLAS_HOME_ASSISTANT_CONTROL_ENABLED": "true",
            "ATLAS_HOME_ASSISTANT_CONTROLLABLE_ENTITY": "light.bedside_table_lamp",
            "ATLAS_HOME_ASSISTANT_BASE_URL": "http://192.168.1.70",
            "ATLAS_HOME_ASSISTANT_TOKEN": "synthetic-token",
        },
        clear=False,
    ), mock.patch.object(
        runner.atlas_home_assistant,
        "control_light",
        return_value={"ok": True, "mismatch": [], "state": {"entity_id": "light.bedside_table_lamp", "state": "on"}},
    ) as mocked:
        happy = runner.run_home_light("turn on the bedside lamp")
    assert happy["result"]["ok"] is True
    assert "on" in happy["answer"].lower()
    mocked.assert_called_once_with(
        "http://192.168.1.70",
        "synthetic-token",
        "light.bedside_table_lamp",
        "turn_on",
        "light.bedside_table_lamp",
        service_data=None,
    )

    # -- run_home_light(): read-after-write mismatch surfaces as a real miss --
    with mock.patch.dict(
        "os.environ",
        {
            "ATLAS_HOME_ASSISTANT_CONTROL_ENABLED": "true",
            "ATLAS_HOME_ASSISTANT_CONTROLLABLE_ENTITY": "light.bedside_table_lamp",
            "ATLAS_HOME_ASSISTANT_BASE_URL": "http://192.168.1.70",
            "ATLAS_HOME_ASSISTANT_TOKEN": "synthetic-token",
        },
        clear=False,
    ), mock.patch.object(
        runner.atlas_home_assistant,
        "control_light",
        return_value={"ok": False, "mismatch": ["state_mismatch"], "state": {"entity_id": "light.bedside_table_lamp", "state": "off"}},
    ):
        mismatch = runner.run_home_light("turn on the bedside lamp")
    assert mismatch["result"]["ok"] is False
    assert "doesn't match" in mismatch["answer"]

    # -- read classifier: positive cases -------------------------------------
    read_positives = [
        "is the lamp on",
        "is the bedside lamp on or off right now",
        "what is the state of the light",
        "what's the lamp's state",
        "is the light currently on",
    ]
    for question in read_positives:
        assert runner.is_home_lamp_state_question(question), f"{question!r} should be a lamp-state question"
        assert runner.classify_question(question) == "home_read", f"{question!r} did not classify as home_read"

    # -- read classifier: must not overlap with the control classifier -------
    control_cases = ["turn on the lamp", "turn the light off", "switch on the light"]
    for question in control_cases:
        assert not runner.is_home_lamp_state_question(question), f"{question!r} incorrectly matched as a state question"
        assert runner.classify_question(question) == "home_light"

    # -- read classifier: negative cases --------------------------------------
    read_negatives = [
        "what is the speed of light",
        "tell me about lamp technology",
        "is it going to rain",
        "what time is it",
        "is the door open",
    ]
    for question in read_negatives:
        assert not runner.is_home_lamp_state_question(question), f"{question!r} incorrectly matched"
        assert runner.classify_question(question) != "home_read"

    # -- run_home_lamp_state(): reads disabled -> fail closed -----------------
    with mock.patch.dict("os.environ", {"ATLAS_HOME_ASSISTANT_ENABLED": "false"}, clear=False):
        reads_disabled = runner.run_home_lamp_state("is the lamp on")
    assert reads_disabled["result"] == {"enabled": False}

    # -- run_home_lamp_state(): no light in the allowlist ---------------------
    with mock.patch.dict(
        "os.environ",
        {"ATLAS_HOME_ASSISTANT_ENABLED": "true", "ATLAS_HOME_ASSISTANT_ENTITIES": '["sensor.temperature"]'},
        clear=False,
    ):
        no_light = runner.run_home_lamp_state("is the lamp on")
    assert "don't have an approved light" in no_light["answer"]

    # -- run_home_lamp_state(): happy path, mocked fetch_state ----------------
    with mock.patch.dict(
        "os.environ",
        {
            "ATLAS_HOME_ASSISTANT_ENABLED": "true",
            "ATLAS_HOME_ASSISTANT_BASE_URL": "http://192.168.1.70",
            "ATLAS_HOME_ASSISTANT_TOKEN": "synthetic-token",
            "ATLAS_HOME_ASSISTANT_ENTITIES": '["light.bedside_table_lamp"]',
        },
        clear=False,
    ), mock.patch.object(
        runner.atlas_home_assistant,
        "fetch_state",
        return_value={
            "entity_id": "light.bedside_table_lamp",
            "state": "on",
            "attributes": {"friendly_name": "Bedside Table Lamp"},
            "source": "home_assistant",
            "read_only": True,
        },
    ) as mocked_fetch:
        read_result = runner.run_home_lamp_state("is the lamp on")
    assert read_result["result"]["state"] == "on"
    assert "Bedside Table Lamp is currently on" in read_result["answer"]
    mocked_fetch.assert_called_once_with(
        "http://192.168.1.70", "synthetic-token", "light.bedside_table_lamp", {"light.bedside_table_lamp"}
    )

    # -- color/effect extension (added after discovering this bulb's real ---
    # hardware supports full RGB + built-in effects; see DEC-2026-09-05-11's
    # follow-up decision and atlas_home_assistant.py's KNOWN_EFFECTS comment).

    # Classifier positives: color.
    color_positives = [
        ("set the lamp to red", (255, 0, 0)),
        ("make the light blue", (0, 0, 255)),
        ("turn the lamp purple", (128, 0, 128)),
        ("change the bedside lamp to green", (0, 255, 0)),
        ("set the lamp color to warm white", (255, 223, 186)),
    ]
    for question, expected in color_positives:
        got = runner._home_light_requested_color(question)
        assert got == expected, f"{question!r} -> {got!r}, expected {expected!r}"
        assert runner.classify_question(question) == "home_light"

    # Classifier positives: effect.
    effect_positives = [
        ("set the lamp to rainbow", "rainbow"),
        ("set the light to happy holidays", "happy_holidays"),
        ("turn on the lamp to vegas mode", "vegas"),
    ]
    for question, expected in effect_positives:
        got = runner._home_light_requested_effect(question)
        assert got == expected, f"{question!r} -> {got!r}, expected {expected!r}"
        assert runner.classify_question(question) == "home_light"

    # Real bug caught before shipping: an effect's spoken form can legitimately
    # start with a plain color word ("red white blue" starts with "red"), so
    # the color classifier must defer to the effect classifier, not the other
    # way around -- this must resolve as the effect, never as plain red.
    assert runner._home_light_requested_color("set lamp to red white blue") is None
    assert runner._home_light_requested_effect("set lamp to red white blue") == "red_white_blue"

    # Negative cases -- must not misfire on unrelated turn-on/turn-off/color text.
    color_effect_negatives = [
        "what is the speed of light",
        "turn on the tv",
        "is the lamp on",
        "make dinner",
        "set the table",
        "change the channel",
        "turn on the fireworks display downtown",
        "what color is the sky",
    ]
    for question in color_effect_negatives:
        assert runner._home_light_requested_color(question) is None, question
        assert runner._home_light_requested_effect(question) is None, question

    # run_home_light() dispatch: color, effect, and on/off all reach
    # control_light() with the right resolved_action/service_data shape.
    control_env = {
        "ATLAS_HOME_ASSISTANT_CONTROL_ENABLED": "true",
        "ATLAS_HOME_ASSISTANT_CONTROLLABLE_ENTITY": "light.bedside_table_lamp",
        "ATLAS_HOME_ASSISTANT_BASE_URL": "http://192.168.1.70",
        "ATLAS_HOME_ASSISTANT_TOKEN": "synthetic-token",
    }
    with mock.patch.dict("os.environ", control_env, clear=False), mock.patch.object(
        runner.atlas_home_assistant,
        "control_light",
        return_value={"ok": True, "mismatch": [], "state": {"state": "on", "attributes": {"rgb_color": [255, 0, 0]}}},
    ) as mocked_color:
        color_result = runner.run_home_light("set the lamp to red")
    assert color_result["result"]["ok"] is True
    mocked_color.assert_called_once_with(
        "http://192.168.1.70",
        "synthetic-token",
        "light.bedside_table_lamp",
        "turn_on",
        "light.bedside_table_lamp",
        service_data={"rgb_color": [255, 0, 0]},
    )

    with mock.patch.dict("os.environ", control_env, clear=False), mock.patch.object(
        runner.atlas_home_assistant,
        "control_light",
        return_value={"ok": True, "mismatch": [], "state": {"state": "on", "attributes": {"effect": "rainbow"}}},
    ) as mocked_effect:
        effect_result = runner.run_home_light("set the lamp to rainbow")
    assert effect_result["result"]["ok"] is True
    assert "rainbow" in effect_result["answer"]
    mocked_effect.assert_called_once_with(
        "http://192.168.1.70",
        "synthetic-token",
        "light.bedside_table_lamp",
        "turn_on",
        "light.bedside_table_lamp",
        service_data={"effect": "rainbow"},
    )

    with mock.patch.dict("os.environ", control_env, clear=False), mock.patch.object(
        runner.atlas_home_assistant,
        "control_light",
        return_value={"ok": True, "mismatch": [], "state": {"state": "on"}},
    ) as mocked_plain:
        plain_result = runner.run_home_light("turn on the lamp")
    assert plain_result["result"]["ok"] is True
    mocked_plain.assert_called_once_with(
        "http://192.168.1.70",
        "synthetic-token",
        "light.bedside_table_lamp",
        "turn_on",
        "light.bedside_table_lamp",
        service_data=None,
    )

    # -- Theme interpretation fallback (added after the owner asked for -----
    # open-ended requests like "do SFSU colors!" -- a model call, but its
    # output is only ever allowed to select from the same fixed
    # NAMED_COLORS/KNOWN_EFFECTS sets used everywhere else; see
    # run_home_light_theme() and its DEC entry for the fail-closed design.

    # Classifier positives, including the exact motivating example (no lamp
    # noun) and lamp-anchored phrasings (which do require the noun).
    theme_positives = [
        ("do SFSU colors!", "sfsu"),
        ("do sfsu colors", "sfsu"),
        ("do SFSU colors on my lamp", "sfsu"),
        ("give the lamp a cozy autumn vibe", "cozy autumn"),
        ("make the lamp look like a sunset", "a sunset"),
        ("set the lamp to a halloween vibe", "halloween"),
    ]
    for question, expected_substr in theme_positives:
        got = runner._home_light_requested_theme(question)
        assert got is not None, f"{question!r} should have matched a theme, got None"
        assert expected_substr in got, f"{question!r} -> {got!r}, expected to contain {expected_substr!r}"
        assert runner.classify_question(question) == "home_light"

    # Real bugs caught before shipping, both from the shared _HOME_LIGHT_NOUN
    # pattern and the "do <theme> colors" pattern specifically.
    assert runner._home_light_requested_action("turn on my lamp") == "turn_on"
    assert runner._home_light_requested_color("set my lamp to red") == (255, 0, 0)
    assert runner.is_home_lamp_state_question("is my lamp on")

    theme_negatives = [
        "do American colors have deeper cultural meaning?",
        "set the lamp to red",
        "set the lamp to rainbow",
        "is the lamp on",
        "turn on the lamp",
        "what is the speed of light",
        "do you like colors",
        "do i like colors",
        "do we like colors",
        "do they like colors",
        "give me a break",
        "make dinner",
    ]
    for question in theme_negatives:
        assert runner._home_light_requested_theme(question) is None, question

    # run_home_light_theme(): validates the model's JSON response before
    # trusting it -- never lets raw model text become an rgb_color/effect.
    def mock_answer_with_model_returning(text):
        def _mock(**kwargs):
            return text, "mock-model"
        return _mock

    common_kwargs = dict(
        base_url="http://open-webui:8080",
        db_path=pathlib.Path("/tmp/fake.db"),
        model=None,
        timeout=60,
        system_prompt_file=pathlib.Path("/tmp/fake-prompt.md"),
    )

    with mock.patch.object(runner, "answer_with_model", mock_answer_with_model_returning('{"type": "color", "value": "purple"}')):
        resolved = runner.run_home_light_theme("SFSU", **common_kwargs)
    assert resolved == {"type": "color", "rgb": (128, 0, 128)}

    with mock.patch.object(runner, "answer_with_model", mock_answer_with_model_returning('{"type": "effect", "value": "rainbow"}')):
        resolved_effect = runner.run_home_light_theme("pride", **common_kwargs)
    assert resolved_effect == {"type": "effect", "name": "rainbow"}

    with mock.patch.object(runner, "answer_with_model", mock_answer_with_model_returning('{"type": "none"}')):
        assert runner.run_home_light_theme("the concept of democracy", **common_kwargs) is None

    with mock.patch.object(runner, "answer_with_model", mock_answer_with_model_returning("not valid json at all")):
        assert runner.run_home_light_theme("anything", **common_kwargs) is None

    # The model naming a plausible-but-wrong value (hallucination) must
    # still fail closed -- never trust the model's raw text over the actual
    # approved sets.
    with mock.patch.object(runner, "answer_with_model", mock_answer_with_model_returning('{"type": "color", "value": "turquoise"}')):
        assert runner.run_home_light_theme("ocean", **common_kwargs) is None
    with mock.patch.object(runner, "answer_with_model", mock_answer_with_model_returning('{"type": "effect", "value": "not_a_real_effect"}')):
        assert runner.run_home_light_theme("something", **common_kwargs) is None

    # run_home_light() end-to-end: theme resolves to a color, reaches
    # control_light() with the correct rgb_color, and the answer references
    # the original theme text.
    with mock.patch.dict("os.environ", control_env, clear=False), mock.patch.object(
        runner, "run_home_light_theme", return_value={"type": "color", "rgb": (128, 0, 128)}
    ), mock.patch.object(
        runner.atlas_home_assistant,
        "control_light",
        return_value={"ok": True, "mismatch": [], "state": {"state": "on", "attributes": {"rgb_color": [128, 0, 128]}}},
    ) as mocked_theme_color:
        theme_result = runner.run_home_light(
            "do SFSU colors!",
            openwebui_base_url="http://open-webui:8080",
            db_path=pathlib.Path("/tmp/fake.db"),
            model=None,
            model_timeout=60,
            system_prompt_file=pathlib.Path("/tmp/fake-prompt.md"),
        )
    assert theme_result["result"]["ok"] is True
    # "sfsu" lowercase -- _home_light_requested_theme() lowercases everything
    # during extraction, so the answer references the lowercased theme text.
    assert "sfsu" in theme_result["answer"].lower()
    mocked_theme_color.assert_called_once_with(
        "http://192.168.1.70",
        "synthetic-token",
        "light.bedside_table_lamp",
        "turn_on",
        "light.bedside_table_lamp",
        service_data={"rgb_color": [128, 0, 128]},
    )

    # run_home_light(): theme resolution fails (None) -> honest "couldn't
    # find a match" answer, control_light() never called. Note: "do the
    # colors of a democracy" (a natural phrasing of this idea) does NOT
    # actually match _home_light_requested_theme() at all -- its trailing
    # "of a democracy" content breaks the classifier's end-anchor, the same
    # anchor that protects against "do American colors have deeper cultural
    # meaning?" earlier. Using "do democracy colors" instead, which does
    # match the classifier, so this test actually exercises the mocked
    # run_home_light_theme() path rather than short-circuiting earlier.
    with mock.patch.dict("os.environ", control_env, clear=False), mock.patch.object(
        runner, "run_home_light_theme", return_value=None
    ), mock.patch.object(
        runner.atlas_home_assistant, "control_light", side_effect=AssertionError("must not be called")
    ):
        no_match_result = runner.run_home_light(
            "do democracy colors",
            openwebui_base_url="http://open-webui:8080",
            db_path=pathlib.Path("/tmp/fake.db"),
            model=None,
            model_timeout=60,
            system_prompt_file=pathlib.Path("/tmp/fake-prompt.md"),
        )
    assert "couldn't find a good" in no_match_result["answer"]

    # run_home_light(): control enabled, but a themed request with no model
    # config passed at all (openwebui_base_url/db_path/system_prompt_file
    # all None/default) fails closed instead of crashing or silently
    # skipping the theme. Control must be enabled here so this actually
    # reaches the theme-specific config check rather than failing earlier
    # at the "control isn't enabled" gate.
    with mock.patch.dict("os.environ", control_env, clear=False):
        unconfigured_result = runner.run_home_light("do SFSU colors!")
    assert "configured" in unconfigured_result["answer"].lower()

    print("ATLAS home-light-control test passed")


if __name__ == "__main__":
    main()
