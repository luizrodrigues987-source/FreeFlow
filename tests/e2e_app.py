"""App-level run of the whole pipeline: a second FreeFlow instance in this process with its own config
(hotkey Ctrl+Alt+Shift+F9, no sounds, no muting, no AI clean-up), a scripted speech engine, and every
output captured - nothing is typed, pasted or put on the real clipboard, no window is activated.

Covers game chat, the spoken correction (whole sentence, one word, and a sentence that only starts with
"correction"), learned rules, the window context backup against a real text box, and a target window
that was closed while the text was being transcribed.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import tempfile
import threading
import time

from _util import ROOT  # noqa: F401  (puts the project on sys.path)

# the test config must be in place BEFORE anything from freeflow is imported: the config path is fixed at
# import time, and the real config would mean the real hotkey, real muting and the real text model
TMP = tempfile.mkdtemp(prefix="ff-e2e-")
cfg = {}
cfg.update({"hotkey": "ctrl+alt+shift+f9", "sounds": False, "overlay": False, "overlay_mode": "popup",
            "notifications": False, "history_enabled": False, "mute_output": False, "mute_mic_mode": "off",
            "smart_target": True, "polish": "off", "first_run_done": True, "auto_update": False, "autostart": False,
            "game_vocab": True, "chat_open_apps": ["League of Legends.exe"], "chat_send_apps": [],
            "game_vocabulary": ["Bora"], "window_context": True, "learning": True, "learn_from_edits": False})
cfg_path = os.path.join(TMP, "config.json")
json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), indent=1)
os.environ["FREEFLOW_CONFIG"] = cfg_path

from freeflow import app as appmod, focus, inject, transcribe  # noqa: E402

appmod.acquire_single_instance = lambda: True
appmod.DATA_DIR = TMP

# ---- a real window with a text box holding a "CRM page" ----------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
user32.CreateWindowExW.restype = ctypes.c_void_p
user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.DestroyWindow.argtypes = [ctypes.c_void_p]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p), ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p)]


proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
win = {}
PAGE = ("Contact: Miren Garcia\r\nEmail: miren.garcia@example.com\r\nHi Miren, thanks for your time today.\r\n"
        "Arthur asked about pavers for the driveway; the pavers we quoted are travertine pavers.\r\n"
        "Deal owner Kirsty Moreau will restart the updater; the updater is old.\r\n")


def win_thread():
    wc = WNDCLASSW()
    wc.lpfnWndProc = proc
    wc.lpszClassName = "FreeFlowE2ETest"
    wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
    user32.RegisterClassW(ctypes.byref(wc))
    h = user32.CreateWindowExW(0, "FreeFlowE2ETest", "Miren Garcia - HubSpot", 0x00CF0000, -3000, -3000, 500, 400,
                               None, None, wc.hInstance, None)
    e = user32.CreateWindowExW(0, "EDIT", "", 0x40000000 | 0x10000000 | 0x0004 | 0x1000, 0, 0, 480, 360, h, None,
                               wc.hInstance, None)
    user32.SendMessageW(e, 0x000C, 0, ctypes.c_wchar_p(PAGE))
    gone = user32.CreateWindowExW(0, "FreeFlowE2ETest", "closed soon", 0x00CF0000, -3000, -3000, 100, 100,
                                  None, None, wc.hInstance, None)
    user32.DestroyWindow(gone)                       # a handle that no longer exists
    user32.ShowWindow(h, 4)
    win.update(hwnd=int(h), edit=int(e), gone=int(gone))
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


threading.Thread(target=win_thread, daemon=True).start()
while "edit" not in win:
    time.sleep(0.05)

H = win["hwnd"]
LEAGUE = ("League of Legends.exe", "League of Legends (TM) Client", 1, H)
CLAUDE = ("claude.exe", "Claude", 1, H)
HUB = ("chrome.exe", "Miren Garcia - HubSpot - Google Chrome", 1, H)
GONE = ("Slack.exe", "Old chat - Slack", 1, win["gone"])
# (foreground, transcript, expected text (prefix), method, extras)
SCENES = [
    (LEAGUE, "kaisa is fed, gang mid", "Kai'Sa is fed, gank mid", "chat-type", {}),
    (CLAUDE, "start loco host", "Start loco host", "paste", {}),
    (CLAUDE, "correction start localhost", "Start localhost", "paste", {"undo": True}),
    (CLAUDE, "start loco host please", "Start localhost please", "paste", {}),
    (HUB, "hi Myron thanks for the call", "Hi Miren thanks for the call", "paste",
     {"probs": {"Myron": 0.95}, "again": "Hi Miren, thanks for the call."}),
    (HUB, "Arthur wants the papers installed by friday", "Arthur wants the pavers installed by friday", "paste",
     {"probs": {"papers": 0.41}, "again": "Arthur wants the pavers installed by Friday."}),
    (HUB, "Arthur wants the papers installed by friday", "Arthur wants the papers installed by friday", "paste",
     {"probs": {"papers": 0.97}}),
    (HUB, "hey Christy this was a lead", "Hey Christy this was a lead", "paste",
     {"probs": {"Christy": 0.7}, "again": "Hey Christy, this was a lead."}),
    (HUB, "we need to update the app", "We need to update the app", "paste", {"probs": {"update": 0.99}}),
    (CLAUDE, "privacy laws are very loosely informed.", "Privacy laws are very loosely informed.", "paste", {}),
    (CLAUDE, "correction, enforced.", "Privacy laws are very loosely enforced.", "paste", {"undo": True}),
    (CLAUDE, "correction the call is at 3 not 2", "Correction the call is at 3 not 2", "paste", {}),
    (GONE, "see you there", None, "clipboard", {}),
]
box = {"i": 0, "prompts": [], "again": []}


def fake_transcribe(self, audio, language="en", prompt=None, details=None):
    if details is None:                               # the second listen of the scene that just ran
        box["again"].append(box["i"] - 1)
        return SCENES[box["i"] - 1][4]["again"]
    box["prompts"].append(prompt or "")
    scene = SCENES[box["i"]]
    box["i"] += 1
    probs = scene[4].get("probs", {})
    details["words"] = [{"word": w, "prob": probs.get(w, 0.97), "start": k * 0.3, "end": k * 0.3 + 0.25}
                        for k, w in enumerate(scene[1].split())]
    return scene[1]


transcribe.LocalWhisperEngine.load = lambda self: self.ready.set()
transcribe.LocalWhisperEngine.transcribe = fake_transcribe
captured = []
fg = {"info": SCENES[0][0]}
appmod.press_enter = lambda: captured.append(("enter",))
appmod.inject_text = lambda text, method, *a, **k: captured.append(("inject", text, method)) or True
appmod.inject_game_chat = lambda text, how, *a, **k: captured.append(("inject", text, "chat-" + how)) or True
appmod.send_undo = lambda: captured.append(("undo",))
appmod.send_backspaces = lambda n: captured.append(("backspace", n))
appmod.wait_modifiers_released = lambda *a, **k: True
appmod.foreground_window_info = lambda: fg["info"]
inject.clipboard_set_text = lambda text, private=False: captured.append(("clipboard", text)) or True
focus.inspect_editable = lambda exe, extra=None: (None, {})
focus.activate_window = lambda hwnd, timeout=1.2: True
focus.user32.GetForegroundWindow = lambda: fg["info"][3]


def driver():
    a = box["app"]
    if a.cfg.get("hotkey") != "ctrl+alt+shift+f9" or a.cfg.get("mute_output") or a.cfg.get("polish") != "off":
        print("NOT THE TEST CONFIG - stopping before anything happens")
        os._exit(3)
    end = time.monotonic() + 30
    while time.monotonic() < end and not (a.engine is not None and a.engine.ready.is_set()):
        time.sleep(0.1)
    a.tray.notify = lambda msg, title="": None
    a._own_window = lambda h: False                   # the test page lives in this process; treat it as foreign
    time.sleep(0.8)
    for scene in SCENES:
        fg["info"] = scene[0]
        before = len(captured)
        a.request("activate", "press", time.monotonic())
        time.sleep(1.2)
        a.request("deactivate", time.monotonic())
        end = time.monotonic() + 15
        while time.monotonic() < end and not any(c[0] in ("inject", "clipboard") for c in captured[before:]):
            time.sleep(0.05)
        time.sleep(0.3)
    box["rules"] = [(r["from"], r["to"]) for r in a.learner.rules]
    a.ui(a.quit)


app = appmod.App()
box["app"] = app
threading.Thread(target=driver, daemon=True).start()
app.run()

print("CAPTURED:", captured)
print("RULES:", box["rules"], "| SECOND LISTENS:", box["again"])
outs = [c for c in captured if c[0] in ("inject", "clipboard")]
assert len(outs) == len(SCENES), (len(outs), len(SCENES))
for k, (fgi, spoken, expect, method, extra) in enumerate(SCENES):
    kind = outs[k][0] if method == "clipboard" else outs[k][2]
    assert kind == method, (k, outs[k], method)
    if expect is not None:
        assert outs[k][1].startswith(expect), (k, outs[k], expect)
assert captured[0] == ("enter",), captured[:2]                                  # game: Enter first
assert box["prompts"][0].endswith("gank, Bora."), box["prompts"][0][-40:]       # game words + own word
undos = [i for i, c in enumerate(captured) if c == ("undo",)]
assert len(undos) == 2, captured                                                # both corrections took the text back
assert ("loco host", "localhost") in box["rules"], box["rules"]
assert not any(w in p for p in box["prompts"] for w in ("Miren", "pavers", "Kirsty")), "window words stay out"
assert box["again"] == [4, 5, 7], box["again"]                                  # second listens only when in doubt
assert outs[-1] == ("clipboard", "See you there "), outs[-1]                    # closed window: clipboard, no paste
print("E2E APP OK")
