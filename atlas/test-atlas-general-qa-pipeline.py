#!/usr/bin/env python3

"""Focused tests for the generalized ATLAS public-QA pipeline."""

import importlib.util
import pathlib
import sys
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "tools" / "ai"
sys.path.insert(0, str(AI_DIR))
SPEC = importlib.util.spec_from_file_location("atlas_runner", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(runner)


def model_answer(prompt, **_kwargs):
    if "Decide whether the current comparison" in prompt:
        if "Current question:\nWhat is the brightest star?" in prompt:
            return '{"needs_clarification": true, "question": "Do you mean apparent brightness from Earth or intrinsic luminosity?"}', "mock-model"
        return '{"needs_clarification": false, "question": ""}', "mock-model"
    if "Answer the user's public question using the search evidence" in prompt:
        return "R136a1 is among the most intrinsically luminous stars known.", "mock-model"
    if "Answer the public-reference question using only the encyclopedia context" in prompt:
        return "R136a1 is among the most intrinsically luminous stars known.", "mock-model"
    if "Write one concise web-search query" in prompt:
        return "most intrinsically luminous known star", "mock-model"
    if "Rewrite the current short follow-up" in prompt and "Short follow-up:\nAbsolute" in prompt:
        return "Which star has the greatest intrinsic luminosity?", "mock-model"
    if "Current question:\nWhich star has the greatest intrinsic luminosity?" in prompt:
        assert "user: What is the brightest star?" in prompt
        return "Intrinsic luminosity depends on the ranking and measurement used.", "mock-model"
    if "Current question:\nWhat is the brightest star, using Absolute as the criterion?" in prompt:
        assert "user: What is the brightest star?" in prompt
        return "R136a1 is among the most intrinsically luminous stars known.", "mock-model"
    if "Current question:\nWhat is basalt?" in prompt:
        return "Basalt is a dark, fine-grained volcanic rock.", "mock-model"
    if "Current question:\nWho painted Guernica?" in prompt:
        return "Pablo Picasso painted Guernica.", "mock-model"
    if "Current question:\nMorgan owns a cabin." in prompt:
        assert "begin the answer with exactly one option copied from Choices" in prompt
        assert "not merely because it was absent from the prompt" in prompt
        assert "do not break the tie using nearest-noun" in prompt
        assert "Do not swap people or reverse nested beliefs" in prompt
        return "no-entailment; ownership alone does not establish residence.", "mock-model"
    if "Current question:\nBrass costs less than copper." in prompt:
        assert "begin the answer with exactly one option copied from Choices" in prompt
        return "copper; copper costs more than brass, which costs more than steel.", "mock-model"
    raise AssertionError(f"unexpected prompt: {prompt}")


def run(question, conversation=None):
    return runner.run_general_reference(
        question=question,
        base_url="http://unused",
        db_path=pathlib.Path("/tmp/unused.db"),
        model=None,
        timeout=1,
        system_prompt_file=pathlib.Path("/tmp/unused-system.md"),
        conversation=conversation or [],
    )


def main():
    structured_routes = {
        "Today is Christmas Eve of 1937. What is the date tomorrow?\nChoices: 12/25/1937; 01/04/1938\nAnswer with the choice and a short reason.": "general_reference",
        "A red book is left of a blue book.\nChoices: red; blue\nAnswer with the choice and a short reason.": "general_reference",
        "A person plays the drums. Does the premise entail the hypothesis?\nChoices: entailment; non-entailment\nAnswer with the choice and a short reason.": "general_reference",
        "Four planets have oceans. Therefore water exists outside Earth.\nChoices: entailment; no-entailment\nAnswer with the choice and a short reason.": "general_reference",
    }
    for question, expected_route in structured_routes.items():
        assert runner.classify_question(question) == expected_route
    ambiguous_pronouns = [
        "The architect thanked the editor because she finished early.\nChoices: The architect finished; The editor finished; Ambiguous\nAnswer with the choice and a short reason.",
        "The ranger warned the teacher that they needed more time.\nChoices: The ranger needed time; The teacher needed time; Ambiguous\nAnswer with the choice and a short reason.",
        "The curator asked the docent to remember her badge.\nChoices: Curator's badge; Docent's badge; Ambiguous\nAnswer with the choice and a short reason.",
    ]
    for question in ambiguous_pronouns:
        assert runner.deterministic_pronoun_ambiguity(question).startswith("Ambiguous")
    object_control = (
        "The ranger called the teacher and asked them to open the gate.\n"
        "Choices: Asked the ranger; Asked the teacher; Ambiguous\nAnswer with the choice and a short reason."
    )
    assert runner.deterministic_pronoun_ambiguity(object_control) is None
    explicit_gender = (
        "Rhea uses she/her pronouns and Omar uses he/him pronouns. Rhea spoke with Omar because he had the file.\n"
        "Choices: Rhea had it; Omar had it; Ambiguous\nAnswer with the choice and a short reason."
    )
    assert runner.deterministic_pronoun_ambiguity(explicit_gender) is None
    assert runner.classify_question("Tell me about the book Dune") == "book"
    assert runner.classify_question("What's the weather in Portland?") == "weather"
    assert runner.classify_question("What is 15% of 240?") == "math"
    assert runner.deterministic_public_search_query(
        "Search current public sources for the latest stable Python release and name the version you find."
    ) == "latest stable Python release"
    release_answer, release_evidence = runner.authoritative_latest_release_answer(
        "What is the latest stable Python release?",
        {
            "results": [
                {"title": "Development", "content": "Future Python 3.16 is under development.", "url": "https://devguide.python.org/versions/"},
                {"title": "Download Python", "content": "Python 3.14.3 released Feb. 3, 2026.", "url": "https://www.python.org/downloads/"},
                {"title": "Source releases", "content": "Latest Python 3 Release - Python 3.14.7.", "url": "https://www.python.org/downloads/source/"},
                {"title": "A blog", "content": "Python 9.0 is newest.", "url": "https://example.com/blog"},
            ]
        },
    )
    assert "3.14.7" in release_answer
    assert release_evidence["domain"] == "python.org"
    unrelated_answer, _ = runner.authoritative_latest_release_answer(
        "What is the latest stable Python release?",
        {"results": [{"title": "A blog", "content": "Python 99.0 released.", "url": "https://example.com/"}]},
    )
    assert unrelated_answer is None

    with mock.patch.object(runner, "answer_with_model", side_effect=model_answer), mock.patch.object(
        runner,
        "run_json_tool",
        return_value={"source": "mock-search", "answers": [], "infoboxes": [], "results": []},
    ):
        basalt = run("What is basalt?")
        guernica = run("Who painted Guernica?")
        brightest = run("What is the brightest star?")
        followup = run(
            "Absolute",
            [
                {"role": "user", "text": "What is the brightest star?"},
                {"role": "assistant", "text": "Do you mean apparent or absolute brightness?"},
            ],
        )
        structured = run(
            "Morgan owns a cabin. Therefore Morgan lives in the cabin.\n"
            "Choices: entailment; no-entailment\n"
            "Answer with the choice and a short reason."
        )
        structured_superlative = run(
            "Brass costs less than copper. Steel costs less than brass. Which is most expensive?\n"
            "Choices: steel; brass; copper\n"
            "Answer with the choice and a short reason."
        )

    assert basalt["tool"] == "openwebui-chat"
    assert "volcanic rock" in basalt["answer"]
    assert "Picasso" in guernica["answer"]
    assert "luminous" in followup["answer"]
    assert brightest["route"] == "clarification"
    assert structured["route"] == "general_reference"
    assert structured["answer"].startswith("no-entailment"), structured
    assert structured_superlative["route"] == "general_reference"
    assert structured_superlative["answer"].startswith("copper"), structured_superlative
    with mock.patch.object(
        runner, "run_json_tool", return_value={"capital": {"label": "Novaport"}}
    ), mock.patch.object(runner, "answer_with_model") as capital_model:
        capital_city = run("What is the capital city of Exampleland?")
    assert capital_city["route"] == "capital"
    assert capital_city["answer"] == "Novaport"
    capital_model.assert_not_called()
    with mock.patch.object(
        runner,
        "answer_with_model",
        side_effect=[
            ('{"name":"query_knowledge_files","arguments":{"query":"test"}}', "mock-model"),
            ("plain answer", "mock-model"),
        ],
    ) as answer_model:
        recovered_answer, _ = runner.answer_general_knowledge(
            question="State a stable fact.",
            conversation=[],
            base_url="http://unused",
            db_path=pathlib.Path("/tmp/unused.db"),
            model=None,
            timeout=1,
            system_prompt_file=pathlib.Path("/tmp/unused-system.md"),
        )
    assert recovered_answer == "plain answer"
    assert answer_model.call_count == 2
    assert answer_model.call_args_list[1].kwargs["include_system_prompt"] is False
    president = run("Who is the president?")
    assert president["route"] == "clarification"
    assert "country or organization" in president["answer"]

    tool_calls = []
    def forced_search_tool(command):
        tool_calls.append(command)
        return {
            "source": "SearXNG",
            "answers": [],
            "infoboxes": [],
            "results": [{"title": "Python releases", "content": "Python 3.14.0 is the latest stable release."}],
        }

    def forced_search_model(prompt, **_kwargs):
        assert "Write one concise web-search query" not in prompt
        return "Python 3.14.0 is the latest stable release.", "mock-model"

    with mock.patch.object(runner, "run_json_tool", side_effect=forced_search_tool), mock.patch.object(
        runner, "answer_with_model", side_effect=forced_search_model
    ):
        forced_search = runner.run_general_reference(
            question="Search current public sources for the latest stable Python release and name the version you find.",
            base_url="http://unused",
            db_path=pathlib.Path("/tmp/unused.db"),
            model=None,
            timeout=1,
            system_prompt_file=pathlib.Path("/tmp/unused-system.md"),
            force_public_search=True,
        )
    assert forced_search["result"]["search_query_model"] == "deterministic-capability-query"
    assert tool_calls[0][-1] == "latest stable Python release"
    assert "macOS" not in tool_calls[0][-1]
    assert "3.14.0" in forced_search["answer"]

    source_text = (AI_DIR / "run-atlas-readonly.py").read_text(encoding="utf-8")
    api_text = (AI_DIR / "atlas_shortcuts_server.py").read_text(encoding="utf-8")
    prompt_text = (ROOT / "ai-evals" / "prompts" / "atlas-general-assistant-supplement.md").read_text(encoding="utf-8")
    forbidden_implementation_answers = [
        "Peter Parker is the civilian identity",
        "A philatelist is someone",
        "A classic Singaporean dish is",
        "The giraffe is the tallest",
        "Europe has roughly 740 million",
        "Tommaso’s in North Beach",
    ]
    combined = source_text + api_text + prompt_text
    for answer in forbidden_implementation_answers:
        assert answer not in combined, f"hard-coded public answer returned: {answer}"

    print("ATLAS generalized QA pipeline test passed")


if __name__ == "__main__":
    main()
