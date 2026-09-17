#!/usr/bin/env python3

import importlib.util
import pathlib


MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-readonly.py")
spec = importlib.util.spec_from_file_location("atlas_readonly", MODULE_PATH)
atlas_readonly = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(atlas_readonly)


def assert_state(
    question: str,
    expected_object: str,
    expected_object_location: str,
    expected_container: str,
    expected_container_location: str,
) -> None:
    assert atlas_readonly.is_closed_world_reasoning_question(question), question
    assert atlas_readonly.classify_question(question) == "closed_world_reasoning"
    result = atlas_readonly.run_closed_world_reasoning(question)
    assert result["result_status"] == "ok", result
    assert result["route"] == "closed_world_reasoning", result
    assert result["tool"] == "local-closed-world-reasoning", result
    assert result["result"]["object"] == expected_object, result
    assert result["result"]["object_location"] == expected_object_location, result
    assert result["result"]["container"] == expected_container, result
    assert result["result"]["container_location"] == expected_container_location, result
    assert expected_object_location in result["answer"], result
    assert expected_container_location in result["answer"], result


def main() -> None:
    assert_state(
        "First the purple pen is placed inside the green charger, then the green charger is moved under the silver key, and finally the purple pen is taken out and put on the desk; where is the purple pen now? Where is the green charger now?",
        "purple pen",
        "on the desk",
        "green charger",
        "under the silver key",
    )
    assert_state(
        "First the blue mug is placed inside the red notebook, then the red notebook is moved under the green charger, and finally the blue mug is taken out and put on the desk; where is the blue mug now? Where is the red notebook now?",
        "blue mug",
        "on the desk",
        "red notebook",
        "under the green charger",
    )
    non_match = "What is the capital of Japan?"
    assert not atlas_readonly.is_closed_world_reasoning_question(non_match)
    assert atlas_readonly.classify_question(non_match) != "closed_world_reasoning"
    print("PASS: closed-world temporal container-state reasoning")


if __name__ == "__main__":
    main()
