#!/usr/bin/env python3

"""Focused tests for ATLAS cross-conversation memory Stages 3 and 4.

Stage 3 -- the domain-exclusive semantic fallback (personal_memory route):
- classify_question only routes to personal_memory in personal scope, and
  only for fuzzy recall phrasing, never overriding a more specific domain
  match (weather/homelab/etc.) that already fired.
- run_personal_memory_chat never calls the model when there is nothing to
  recall (no knowledge collection yet, or retrieval returns no documents) --
  the same "cheap common case, grounded when uncertain" contract as the
  tier-1 deterministic handler.
- When retrieval does return documents, the model is called with an
  anti-hallucination instruction and the retrieved text, and the answer is
  extracted from the response.

Stage 4 -- the blended context tier (no network call, quietly informs
ordinary general-knowledge answers):
- blended_memory_context never touches disk outside personal scope or
  without a state file, and returns None (no block) on no overlap.
- When the question overlaps a saved item's tokens, it returns a small
  provenance-labeled block, and run_general_reference threads it into
  answer_general_knowledge's prompt.
- build_general_knowledge_prompt only adds the "Known facts" section, with
  its anti-invention instruction, when a memory_context is actually present.

Stage 4 continuation -- found while reviewing the rest of the codebase for
the same class of gap: atlas_shortcuts_server.py's pre-existing
comparison/recommendation helpers already consulted saved preferences, but
never checked context_scope at all, so a non-personal conversation could
have gotten personal preferences folded into scoring/text. Fixed with the
same scope gate as everything else in this feature.
"""

import importlib.util
import json
import pathlib
import sys
import tempfile
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "tools" / "ai"
sys.path.insert(0, str(AI_DIR))
SPEC = importlib.util.spec_from_file_location("atlas_runner_memory", AI_DIR / "run-atlas-readonly.py")
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(runner)

SERVER_SPEC = importlib.util.spec_from_file_location("atlas_shortcuts_server_memory", AI_DIR / "atlas_shortcuts_server.py")
server = importlib.util.module_from_spec(SERVER_SPEC)
assert SERVER_SPEC.loader
SERVER_SPEC.loader.exec_module(server)


def main():
    # -- classify_question routing --------------------------------------
    assert runner.classify_question("Do you remember my favorite color?", "personal") == "personal_memory"
    assert runner.classify_question("Did I ever mention my allergy to shellfish?", "personal") == "personal_memory"
    assert runner.classify_question("What are my preferences?", "personal") == "personal_memory"
    assert runner.classify_question("Do you know my birthday?", "personal") == "personal_memory"

    # Not personal scope -> never routes to personal_memory, even with
    # identical trigger phrasing.
    assert runner.classify_question("Do you remember my favorite color?", "operations") == "general_reference"
    assert runner.classify_question("Do you remember my favorite color?", "admin") == "general_reference"

    # Domain-exclusive: a more specific classifier that already matched wins,
    # even if incidental recall language is present in the same question.
    assert runner.classify_question("Do you remember what changed on ai-stack?", "personal") == "homelab"
    assert runner.classify_question("Do you remember the weather in Portland?", "personal") == "weather"

    # No trigger phrase at all -> ordinary general_reference, unaffected.
    assert runner.classify_question("What is the capital of France?", "personal") == "general_reference"

    # -- forced-capability path (orchestrator canary) must not bypass this --
    # The orchestrator planner commonly resolves ordinary questions to a
    # "direct" no-capability decision, which the canary executes as forced
    # capability "answer.general" -- not "memory.recall" -- so both must be
    # covered, not just the capability whose name says "memory". Confirmed
    # by reading atlas_orchestrator_v2.deterministic_context_plan: it only
    # hard-routes to "memory.recall" given prior conversation history plus
    # narrow "did i say/mention" phrasing; anything else (including fuzzy
    # "do you remember" phrasing with no history, exactly what tier 1 in
    # atlas_shortcuts_server.py falls through on) reaches the model-based
    # planner, which commonly lands on "answer.general" instead.
    assert runner.is_personal_memory_question("Do you remember how I like my coffee in the morning?")
    assert runner.CAPABILITY_ROUTES["memory.recall"] == "general_reference"  # unchanged default mapping
    assert runner.CAPABILITY_ROUTES["answer.general"] == "general_reference"  # unchanged default mapping

    # -- run_personal_memory_chat: cheap, grounded "nothing to recall" ----
    with mock.patch.object(runner, "load_api_key", return_value="unused-key"), mock.patch.object(
        runner, "api_request", return_value={"items": []}
    ) as api_request, mock.patch.object(runner, "answer_with_model") as model_call:
        no_collection = runner.run_personal_memory_chat(
            question="Do you remember my favorite color?",
            base_url="http://unused",
            db_path=pathlib.Path("/tmp/unused.db"),
            knowledge_name="ATLAS Personal Memory",
            model=None,
            timeout=1,
            system_prompt_file=pathlib.Path("/tmp/unused-system.md"),
        )
    assert no_collection["route"] == "personal_memory"
    assert no_collection["answer"] == "I don't have anything saved about that on file."
    assert no_collection["result"]["knowledge_found"] is False
    model_call.assert_not_called()
    assert api_request.call_count == 1  # only the knowledge lookup -- no retrieval, no model call

    with mock.patch.object(runner, "load_api_key", return_value="unused-key"), mock.patch.object(
        runner, "api_request", return_value={"items": [{"id": "kb-1", "name": "ATLAS Personal Memory"}]}
    ), mock.patch.object(
        runner, "timed_api_request", return_value={"json": {"ids": [], "documents": [], "metadatas": []}, "elapsed_seconds": 0.01}
    ), mock.patch.object(
        runner, "answer_with_model"
    ) as model_call_empty:
        no_documents = runner.run_personal_memory_chat(
            question="Do you know my favorite color?",
            base_url="http://unused",
            db_path=pathlib.Path("/tmp/unused.db"),
            knowledge_name="ATLAS Personal Memory",
            model=None,
            timeout=1,
            system_prompt_file=pathlib.Path("/tmp/unused-system.md"),
        )
    assert no_documents["answer"] == "I don't have anything saved about that on file."
    assert no_documents["result"]["answer_mode"] == "no-documents-retrieved"
    model_call_empty.assert_not_called()

    # -- run_personal_memory_chat: real recall, grounded in retrieved text --
    retrieval_result = {
        "ids": ["note-1"],
        "documents": ["Note (favorite color, saved 2026-09-01): The user's favorite color is teal."],
        "metadatas": [{"name": "favorite color", "source": "ATLAS personal memory"}],
    }
    with mock.patch.object(runner, "load_api_key", return_value="unused-key"), mock.patch.object(
        runner, "api_request", side_effect=[
            {"items": [{"id": "kb-1", "name": "ATLAS Personal Memory"}]},
            {"data": [{"id": "qwen3:8b"}]},
        ]
    ), mock.patch.object(
        runner, "timed_api_request",
        side_effect=[
            {"json": retrieval_result, "elapsed_seconds": 0.02},
            {"json": {"choices": [{"message": {"content": "Your favorite color is teal."}}]}, "elapsed_seconds": 0.3},
        ],
    ):
        matched = runner.run_personal_memory_chat(
            question="Do you know my favorite color?",
            base_url="http://unused",
            db_path=pathlib.Path("/tmp/unused.db"),
            knowledge_name="ATLAS Personal Memory",
            # DEF-008 removed choose_model()'s implicit default (it silently
            # picked an uncensored, safety-training-stripped model); this
            # test's mocked api_request side_effect already returns
            # {"data": [{"id": "qwen3:8b"}]} for the model-list call, so
            # naming that same model explicitly is all that changed -- the
            # mock was already shaped for a real model lookup, just never
            # exercised because model=None short-circuited before DEF-008.
            model="qwen3:8b",
            timeout=1,
            system_prompt_file=pathlib.Path("/tmp/unused-system.md"),
        )
    assert matched["route"] == "personal_memory"
    assert matched["tool"] == "openwebui-chat"
    assert "teal" in matched["answer"]
    assert matched["result"]["knowledge_name"] == "ATLAS Personal Memory"

    # -- build_personal_memory_prompt carries the anti-hallucination line --
    prompt = runner.build_personal_memory_prompt(
        "Do you know my favorite color?",
        [{"name": "favorite color", "text": "The user's favorite color is teal."}],
    )
    assert "do not guess or invent details" in prompt
    assert "teal" in prompt

    # -- Stage 4: blended context ----------------------------------------
    # Never touches disk when scope isn't personal or no state file is given.
    with mock.patch.object(runner.atlas_memory_corpus, "read_state_cache") as read_cache:
        assert runner.blended_memory_context("Do I like coffee?", "operations", pathlib.Path("/tmp/unused-state.json")) is None
        assert runner.blended_memory_context("Do I like coffee?", "personal", None) is None
    read_cache.assert_not_called()

    with tempfile.TemporaryDirectory() as tmp:
        state_path = pathlib.Path(tmp) / "memory-corpus-state.json"

        # Empty state file (nothing synced yet) -> None, no crash.
        state_path.write_text(json.dumps({"items": []}), encoding="utf-8")
        assert runner.blended_memory_context("Do I like coffee?", "personal", state_path) is None

        # Real items, no token overlap with the question -> None (the common
        # case: don't add noise to unrelated questions).
        state_path.write_text(
            json.dumps({"items": [{"type": "preference", "label": "general", "text": "black coffee, no sugar", "updated_at": "2026-09-01T00:00:00"}]}),
            encoding="utf-8",
        )
        assert runner.blended_memory_context("What is the capital of Japan?", "personal", state_path) is None

        # Overlap -> a small provenance-labeled block.
        block = runner.blended_memory_context("What kind of milk do I prefer?", "personal", state_path)
        assert block is not None
        assert "black coffee" in block
        assert "2026-09-01" in block

        # -- build_general_knowledge_prompt: block only appears when passed --
        prompt_without = runner.build_general_knowledge_prompt("What is the capital of Japan?", [])
        assert "Known facts about the user" not in prompt_without
        prompt_with = runner.build_general_knowledge_prompt("What kind of milk do I prefer?", [], block)
        assert "Known facts about the user" in prompt_with
        assert "do not invent additional preferences" in prompt_with
        assert "black coffee" in prompt_with

        # -- run_general_reference threads memory_context through to
        # answer_general_knowledge for a plain, non-live, non-capital question.
        with mock.patch.object(
            runner, "answer_general_knowledge", return_value=("Black coffee, no sugar.", "mock-model")
        ) as answer_call:
            result = runner.run_general_reference(
                question="What kind of milk do I prefer?",
                base_url="http://unused",
                db_path=pathlib.Path("/tmp/unused.db"),
                model=None,
                timeout=1,
                system_prompt_file=pathlib.Path("/tmp/unused-system.md"),
                conversation=[],
                context_scope="personal",
                memory_state_file=state_path,
            )
        assert answer_call.call_count == 1
        assert answer_call.call_args.kwargs["memory_context"] == block
        assert result["result"]["memory_context_used"] is True

        # Operations scope with the same phrasing and the same state file on
        # disk -> memory_context must never be threaded in outside personal
        # scope, even though the file has matching content.
        with mock.patch.object(
            runner, "answer_general_knowledge", return_value=("Milk preferences vary by person.", "mock-model")
        ) as answer_call_ops:
            result_ops = runner.run_general_reference(
                question="What kind of milk do I prefer?",
                base_url="http://unused",
                db_path=pathlib.Path("/tmp/unused.db"),
                model=None,
                timeout=1,
                system_prompt_file=pathlib.Path("/tmp/unused-system.md"),
                conversation=[],
                context_scope="operations",
                memory_state_file=state_path,
            )
        assert answer_call_ops.call_args.kwargs["memory_context"] is None
        assert result_ops["result"]["memory_context_used"] is False

    # -- Stage 4 continuation: comparison/recommendation scope gate --------
    fake_preferences = [{"key": "privacy", "value": "prefer local-first, self-hosted tools", "updated_at": "2026-09-01"}]
    with mock.patch.object(server.atlas_app_store, "list_preferences", return_value=fake_preferences) as list_prefs:
        personal_comparison = server.AtlasHandler._answer_comparison_request(
            None, "Compare self-hosted ntfy and public cloud push", "personal"
        )
        assert list_prefs.called
        assert personal_comparison["result"]["preferences_used"] == fake_preferences
        # A privacy/local preference should bias the score toward the
        # self-hosted option, proving the preference was actually used, not
        # just carried through unused.
        assert personal_comparison["result"]["winner"] == "self-hosted ntfy"

        list_prefs.reset_mock()
        ops_comparison = server.AtlasHandler._answer_comparison_request(
            None, "Compare self-hosted ntfy and public cloud push", "operations"
        )
        list_prefs.assert_not_called()
        assert ops_comparison["result"]["preferences_used"] == []
        assert ops_comparison["result"]["winner"] == "tie"

        list_prefs.reset_mock()
        personal_recommendation = server.AtlasHandler._answer_recommendation_request(
            None, "Which should I choose?", "personal"
        )
        assert list_prefs.called
        assert personal_recommendation["result"]["preferences_used"] == fake_preferences
        assert "local-first" in personal_recommendation["answer"]

        list_prefs.reset_mock()
        ops_recommendation = server.AtlasHandler._answer_recommendation_request(
            None, "Which should I choose?", "operations"
        )
        list_prefs.assert_not_called()
        assert ops_recommendation["result"]["preferences_used"] == []
        assert "local-first" not in ops_recommendation["answer"]

    print("ATLAS cross-conversation memory Stages 3-4 test passed")


if __name__ == "__main__":
    main()
