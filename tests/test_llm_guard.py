"""The checks every AI clean-up passes before it replaces the transcript (no model needed: the outputs here
are ones the local model really produced, or cases built to the same pattern)."""
from _util import case, run
from freeflow.config import DEFAULTS
from freeflow.llm import _tidy_output, app_kind, build_system, changed_meaning, describe_app, style_for

MUST_REJECT = [
    # it answered the question instead of cleaning it
    ("Why are you asking me to give access to these websites?",
     "I'm not asking you to give access to any websites. Please provide the text you need assistance with."),
    ("What's the best way to follow up with a cold lead?",
     "The best way to follow up with a cold lead is a short personal email."),
    # preambles and sign-offs it invented
    ("Make it like a small file so I can send it over Discord if possible.",
     "Sure, here it is formatted for easy sending over Discord:\n\nMake it like a small file so I can send it over Discord if possible."),
    ("And the icon between board and list view should be like that.",
     "And the icon between board and list view should be like that.\n\nClaude."),
    ("I don't want you to reshape the lead stages at all. I only want you to add a horizontal bar once the settings tab opens up.",
     "I don't want you to reshape the lead stages at all. I only want you to add a horizontal bar once the settings tab opens up.\n\nPlease let me know."),
    ("Anyone have a good...", "Anyone have a good suggestion?"),
    # facts changed
    ("I also want the color ID to be Home Renovations instead of Summit Renovations and the company name to be Summit Renovations too.",
     "I also want the color ID to be Home Renovations instead of Summit Renovations and the company name to be Home Renovations too."),
    ("The order is spacebar press, down arrow press, enter press, right arrow press, enter press, spacebar press, repeat this order.",
     "The order is spacebar press, down arrow press, enter press, right arrow press, enter press, spacebar press, enter press."),
    ("We closed 3 deals last month for a total of $40,000 and the next one is $12,500.",
     "We closed 3 deals last month for a total of $4,000 and the next one is $1,250."),
    ("Tell Mark we don't need the extra seats this quarter, and we can revisit pricing in January.",
     "Tell Mark we need the extra seats this quarter, and we can revisit pricing in January."),
    ("Let's meet on Thursday with Dana.", "Let's meet on Tuesday with Dana."),
]

MUST_ACCEPT = [
    ("so um i think we should uh move the meeting to thursday thursday afternoon works better for me thanks",
     "I think we should move the meeting to Thursday. Thursday afternoon works better for me. Thanks."),
    ("hey what time is the game tonight", "Hey, what time is the game tonight?"),
    ("oh today is wednesday oh i mean it's tuesday actually can we meet at three no wait at four",
     "Today is Tuesday. Can we meet at four?"),
    ("we need milk eggs and bread new line and call the plumber", "We need milk, eggs and bread.\nAnd call the plumber."),
    ("I was thinking we could move the call to Thursday because Wednesday is packed, thanks",
     "I was thinking we could move the call to Thursday because Wednesday is packed. Thanks."),
    ("the meeting is at 5.30 and it costs $40,000", "The meeting is at 5:30, and it costs $40,000."),
    ("first we finish the deck second we send the invoice to Mark",
     "1. Finish the deck.\n2. Send the invoice to Mark."),
    ("I can't, I'm sorry, I'm going to the Rams game tonight.", "I can't, I'm sorry, I'm going to the Rams game tonight."),
    ("we do not need it", "We don't need it."),
]


@case
def rejects_answers_inventions_and_changed_facts():
    for text, out in MUST_REJECT:
        assert _tidy_output(text, out) == text, ("accepted", text, out, changed_meaning(text, out))


@case
def accepts_real_clean_ups():
    for text, out in MUST_ACCEPT:
        assert _tidy_output(text, out) == out, ("rejected", text, out, changed_meaning(text, out))


@case
def apologies_no_longer_loosen_the_guard():
    text = ("Sorry, the contract is with legal, pricing is approved, signatures are due by Friday, "
            "and onboarding starts on the 3rd.")
    assert _tidy_output(text, "Sorry, the contract is with legal and pricing is approved.") == text


@case
def quotes_of_the_text_stay():
    text = '"Hell yeah," he said, "let\'s go."'
    assert _tidy_output(text, text) == text
    assert _tidy_output("hello there friend", '"Hello there, friend."') == "Hello there, friend."


@case
def the_model_never_sees_window_titles():
    assert describe_app("Slack.exe", "Kirsty Moreau (DM) - Acme - Slack") == "a chat message"
    assert describe_app("claude.exe", "Claude").startswith("a message to an AI assistant")
    assert describe_app("chrome.exe", "Inbox (3) - someone@example.com - Gmail") == "an e-mail or a note"
    assert describe_app("chrome.exe", "Some page") == ""
    system = build_system("clean", describe_app("Slack.exe", "Kirsty Moreau (DM) - Slack"), local=True)
    assert "Kirsty" not in system and "chat message" in system


@case
def style_per_kind_of_app():
    assert app_kind("Slack.exe", "general - Slack") == "chat" and style_for(DEFAULTS, "Slack.exe") == "casual"
    assert style_for(DEFAULTS, "chrome.exe", "Deal - HubSpot - Google Chrome") == "formal"
    assert style_for(DEFAULTS, "claude.exe", "Claude") == "light"
    assert style_for(DEFAULTS, "chrome.exe", "Weather") == "clean"                     # other: the general style
    cfg = dict(DEFAULTS, polish_style="casual", polish_style_per_app={"chat": "", "ai": "formal"})
    assert style_for(cfg, "Slack.exe") == "casual" and style_for(cfg, "claude.exe") == "formal"
    assert "Change as little as possible" in build_system("light", "", local=True)


if __name__ == "__main__":
    run()
