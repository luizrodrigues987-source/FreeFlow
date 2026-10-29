"""The window in front as a backup: which words become candidates, how the second listen decides."""
from _util import case, run
from freeflow.context import (WindowContext, extract_names, extract_terms, second_listen_plan, word_probabilities,
                              worth_a_look)

CTX = WindowContext(names=["Miren", "Garcia", "Kirsty", "Moreau", "Steinberg", "Radcliffe", "Andrews", "Slackbot"],
                    terms=["pavers", "updater", "rescheduleid", "travertine"])


def cands(text, probs=None, protected=()):
    return [(c["word"], c["replacement"]) for c in CTX.candidates(text, probs or {}, protected)]


@case
def ordinary_words_heard_confidently_are_never_candidates():
    assert cands("You can use the rescheduling link down below.", {"rescheduling": 0.98}) == []
    assert cands("Anyone else getting these Slack notifications?", {"anyone": 0.16, "slack": 0.17}) == []
    assert cands("We need to update the app before Friday.", {"update": 0.99}) == []
    assert cands("We need to update the app before Friday.", {"update": 0.2}) == []      # prefix pair, even unsure
    assert cands("She's good, there's potential down the road.", {"shes": 0.85}) == []
    assert cands("Some of these things are taking too long to dictate.", {"some": 0.72}) == []
    assert cands("Arthur wants the papers installed by Friday.", {"papers": 0.97}) == []
    assert cands("I have a meeting with Ryan Radliff tomorrow.", {"radliff": 0.99}) == []


@case
def doubtful_or_same_sounding_words_are_candidates():
    assert cands("Arthur wants the papers installed by Friday.", {"papers": 0.41}) == [("papers", "pavers")]
    assert cands("I have a meeting with Ryan Radliff tomorrow.", {"radliff": 0.3}) == [("Radliff", "Radcliffe")]
    assert cands("Hi Myron, thanks for your time today.", {"myron": 0.95}) == [("Myron", "Miren")]
    assert cands("Send it to Stineberg.", {"stineberg": 0.9}) == [("Stineberg", "Steinberg")]
    assert cands("Hey Christy, this was a lead.", {"christy": 0.7}) == [("Christy", "Kirsty")]
    assert cands("We quoted the pavors yesterday.", {"pavors": 0.9}) == [("pavors", "pavers")]
    assert cands("Hi Myron, thanks.", {"myron": 0.95}, protected=["Myron"]) == []       # the user's vocabulary wins
    assert cands("Hi Miren, thanks.", {"miren": 0.2}) == []


@case
def second_listen_decides():
    c = CTX.candidates("Hey Christy, this was a lead.", {"christy": 0.7})
    assert WindowContext.confirmed(c, "Hey Christy, this was a lead.") == []
    ok = WindowContext.confirmed(c, "Hey, Kirsty, this was a lead.")
    assert WindowContext.apply("Hey Christy, this was a lead.", ok) == "Hey Kirsty, this was a lead."
    c = CTX.candidates("Myron's email bounced, so I called Myron.", {"myron": 0.9, "myrons": 0.9})
    ok = WindowContext.confirmed(c, "Miren's email bounced, so I called Miren.")
    assert WindowContext.apply("Myron's email bounced, so I called Myron.", ok) == "Miren's email bounced, so I called Miren."
    assert WindowContext.confirmed(c, "Miren's email bounced, so I called Myron.") == []
    c = CTX.candidates("He wants one papers sample.", {"papers": 0.3})
    ok = WindowContext.confirmed(c, "He wants one paver sample.")
    assert WindowContext.apply("He wants one papers sample.", ok) == "He wants one paver sample."


@case
def probabilities_and_worth():
    probs = word_probabilities([{"word": " Hi", "prob": 0.8}, {"word": " Myron,", "prob": 0.95}, {"word": " Myron's", "prob": 0.6}])
    assert probs == {"hi": 0.8, "myron": 0.6, "myrons": 0.6}, probs
    assert worth_a_look("Hi Myron, thanks.", {}) is True
    assert worth_a_look("No worries at all.", {"no": 0.3, "worries": 0.99}) is False
    assert worth_a_look("Send me the papers.", {"papers": 0.4}) is True


@case
def which_audio_is_heard_again():
    c = CTX.candidates("Hi Myron, thanks.", {"myron": 0.95})
    assert second_listen_plan(c, [], 6.0) == (c, None)
    words = [{"word": "Hi", "prob": 0.8, "start": 30.0, "end": 30.3}, {"word": "Myron,", "prob": 0.95, "start": 30.4, "end": 30.9}]
    plan = second_listen_plan(c, words, 70.0)
    assert plan[0] == c and plan[1] == (25.4, 33.4), plan
    assert second_listen_plan(c, [], 70.0) == ([], None)


@case
def page_reading():
    page = ("Contact: Miren Garcia\nEmail: miren.garcia@example.com\nHi Miren, thanks for your time today.\n"
            "Arthur asked about pavers for the driveway; the pavers we quoted are travertine pavers.\n")
    names = extract_names(page, "Miren Garcia - HubSpot")
    assert names[:2] == ["Miren", "Garcia"], names
    assert "pavers" in extract_terms(page, "") and "driveway" not in extract_terms(page, "")


if __name__ == "__main__":
    run()
