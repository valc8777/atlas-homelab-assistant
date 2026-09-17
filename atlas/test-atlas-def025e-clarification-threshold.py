#!/usr/bin/env python3

import importlib.util
import pathlib
import sys


MODULE_PATH = pathlib.Path(__file__).with_name("run-atlas-readonly.py")
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("atlas_readonly", MODULE_PATH)
atlas_readonly = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(atlas_readonly)


def main() -> None:
    causal = (
        "For the daily briefing, imagine we optimize service monitoring aggressively and accept a little extra latency; "
        "what is the most likely benefit and the most likely downside? If the owner complains that it feels slower, "
        "what measurement would tell us whether the tradeoff is actually worth keeping?"
    )
    evidence = (
        "An answer about private notifications can cite a local audit event and a README, but they disagree on the "
        "current state and the user cares most about privacy exposure; which source should get more weight and why? "
        "What should the assistant say if neither source proves the current answer?"
    )
    still_ambiguous = "What is the most reliable archive?"

    for prompt in (causal, evidence):
        assert atlas_readonly.may_need_comparison_clarification(prompt) is False, prompt
        assert atlas_readonly.deterministic_comparison_clarification(prompt) is None, prompt
    # `causal` fits the causal_tradeoff_reasoning template family exactly
    # (it's the same shape as the seed example that route was built from),
    # so it should route there, not to general_reference -- confirmed this
    # was already true before DEF-026's changes too; this assertion was
    # stale from before that route existed and had never actually been
    # re-verified against the committed code.
    assert atlas_readonly.classify_question(causal) == "causal_tradeoff_reasoning", causal
    assert atlas_readonly.classify_question(evidence) == "evidence_quality_reasoning", evidence

    clarification = atlas_readonly.deterministic_comparison_clarification(still_ambiguous)
    assert clarification and "criterion" in clarification, clarification
    print("PASS: DEF-025e clarification threshold")


if __name__ == "__main__":
    main()
