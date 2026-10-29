"""Learning from corrections: which fixes become rules, how rules are applied, the one-time clean-up."""
import json
import os
import tempfile

from _util import case, run
from freeflow.learning import Learner, extract_corrections, rule_problem, sounds_alike


def fresh(rules=None, **extra):
    d = tempfile.mkdtemp(prefix="ff-test-learn-")
    if rules is not None:
        data = {"rules": rules, "candidates": {}}
        data.update(extra)
        json.dump(data, open(os.path.join(d, "learned.json"), "w", encoding="utf-8"))
    return Learner(d)


@case
def gate_keeps_mishearings():
    for src, dst in [("loco host", "localhost"), ("Luther host", "localhost"), ("Myron", "Miren"),
                     ("free flow", "FreeFlow"), ("Ludiebrium", "Ludibrium"), ("Methus", "Matheus")]:
        assert rule_problem(src, dst) is None, (src, dst, rule_problem(src, dst))


@case
def gate_rejects_edits():
    for src, dst in [("low", "lol"), ("email", "emailed"), ("buttons", "button"), ("sides", "slides"),
                     ("clothes", "close"), ("starts", "is due to start"), ("taking them", "taken"),
                     ("Give me more recommendations", "Type for commands"), ("five months", "5months"),
                     ("these moments", "to use momence"), ("papers", "pavers")]:
        assert rule_problem(src, dst) is not None, (src, dst)
    # a spoken "correction" may fix one ordinary word, but not reword
    assert rule_problem("papers", "pavers", explicit=True) is None
    assert rule_problem("I'm going to give", "Look at", explicit=True) is not None


@case
def apply_is_one_pass():
    L = fresh()
    L.add_rule("a", "b", "manual")
    L.add_rule("b", "c", "manual")               # a -> b is redirected to c when b -> c is learned
    assert L.apply("a b") == "c c", L.apply("a b")
    L.add_rule("Karen", "Kern", "manual")
    L.add_rule("Kern", "Keryn", "manual")
    assert {r["from"]: r["to"] for r in L.rules}["Karen"] == "Keryn"


@case
def apply_leaves_compounds_alone():
    L = fresh()
    L.add_rule("low", "lol", "manual")
    out = L.apply("The price is low, high-low and low-key; low.")
    assert out == "The price is lol, high-low and low-key; lol.", out
    L.add_rule("loco host", "localhost", "manual")
    assert L.apply("start Loco  host now") == "start Localhost now"


@case
def declaring_a_word_right_removes_its_rule():
    L = fresh()
    L.add_rule("Kern", "Keryn", "manual")
    L.add_rule("Karen", "Kern", "manual")        # the user now says "Kern" is right
    assert {r["from"]: r["to"] for r in L.rules} == {"Karen": "Kern"}, L.rules


@case
def typed_fix_needs_two_sightings():
    L = fresh()
    assert L.learn_typed("Luther host", "localhost") == ([], [("Luther host", "localhost")])
    assert not L.rules
    assert L.learn_typed("Luther host", "localhost") == ([("Luther host", "localhost")], [])
    assert L.rules[0]["source"] == "typing"
    assert L.learn_typed("email", "emailed") == ([], [])


@case
def spoken_correction_learns_only_the_mishearing():
    L = fresh()
    assert L.learn("start loco host", "start localhost", "voice") == [("loco host", "localhost")]
    assert L.learn("I'm going to give you the file", "Look at the file", "voice") == []


@case
def old_bad_rules_are_retired_once():
    rules = [{"from": "low", "to": "lol", "source": "typing", "hits": 4},
             {"from": "email", "to": "emailed", "source": "typing", "hits": 1},
             {"from": "Myron", "to": "Miren", "source": "typing", "hits": 1},
             {"from": "Janine", "to": "Jeneen", "source": "typing"},
             {"from": "Jeneen", "to": "Jenn", "source": "typing"},
             {"from": "wasp", "to": "WASP", "source": "manual"}]          # added by hand: never second-guessed
    L = fresh(rules)
    kept = {r["from"]: r["to"] for r in L.rules}
    assert kept == {"Myron": "Miren", "Janine": "Jenn", "Jeneen": "Jenn", "wasp": "WASP"}, kept
    assert sorted(r["from"] for r in L.retired) == ["email", "low"]
    data = json.load(open(L.path, encoding="utf-8"))
    assert data["gate_v2"] is True and len(data["retired"]) == 2
    again = Learner(os.path.dirname(L.path))     # no second migration
    assert len(again.rules) == 4 and len(again.retired) == 2


@case
def extract_and_sound():
    assert extract_corrections("start loco host", "start localhost") == [("loco host", "localhost")]
    assert sounds_alike("Myron", "Miren") and not sounds_alike("John", "Jane")


if __name__ == "__main__":
    run()
