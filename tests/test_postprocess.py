"""Rule-based clean-up: fillers, voice commands, automatic lists, capitals, the hallucination filter."""
from _util import case, run
from freeflow.config import DEFAULTS
from freeflow.gamevocab import correct_game_text
from freeflow.postprocess import clean_transcript, looks_like_hallucination


def clean(text):
    return clean_transcript(text, DEFAULTS)


@case
def quantities_are_not_lists():
    t = "We closed one deal in May and two deals in June, then three in July."
    assert clean(t) == t
    assert clean("I sent one email Monday, two emails Tuesday and three emails today.").count("\n") == 0


@case
def spoken_lists_still_work():
    out = clean("I need three changes. One, fix the login. Two, add the export. Three, update the docs.")
    assert out == "I need three changes:\n1. Fix the login.\n2. Add the export.\n3. Update the docs.", out
    out = clean("First one is HubSpot, which I'm leaning more towards, and then second one is Moment.")
    assert out == "1. HubSpot, which I'm leaning more towards\n2. Moment.", out


@case
def formatting_words_inside_sentences_stay():
    for t in ("We launched a new line.", "Can you start a new paragraph", "I need one more bullet.",
              "Our new line, of products is great", "Add a bullet point."):
        assert clean(t) == t, (t, clean(t))
    assert clean("Thanks for that. New paragraph. See you tomorrow.") == "Thanks for that.\n\nSee you tomorrow."
    assert clean("Here is the list. Bullet point call Mark bullet point email Jane") == \
        "Here is the list.\n- Call Mark\n- Email Jane"


@case
def capitals_and_fillers():
    assert clean("iPhone is great") == "iPhone is great"
    assert clean("eBay listing is up") == "eBay listing is up"
    assert clean("Um... what?") == "What?"
    assert clean("So, uh... yeah") == "So, yeah"
    assert clean("The U.S. um, market is big") == "The U.S. market is big"


@case
def short_replies_survive_a_quiet_microphone():
    assert not looks_like_hallucination("Thanks!", rms=0.008, duration=1.5)
    assert not looks_like_hallucination("Okay.", rms=0.006, duration=1.0)
    assert looks_like_hallucination("Thank you.", rms=0.008, duration=0.4)              # an accidental tap
    assert looks_like_hallucination("Thank you.", rms=0.01, duration=3.0, no_speech=0.9)
    assert looks_like_hallucination("Thanks for watching!", rms=0.0005, duration=2.0)   # silence
    assert not looks_like_hallucination("Thank you, Mark.", rms=0.0005, duration=0.4)   # not a stock phrase


@case
def game_chat_terms():
    assert correct_game_text("I had to alt tab, my alt is up, alt-tab again") == \
        "I had to alt tab, my ult is up, alt-tab again"


if __name__ == "__main__":
    run()
