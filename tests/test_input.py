"""Keyboard and clipboard logic with the system calls replaced: no hook is installed, no key is sent and
the real clipboard is never touched."""
import threading
import time

from _util import case, run
from freeflow import hotkeys, inject

VK_LCONTROL, VK_LWIN, VK_C = 0xA2, 0x5B, 0x43


class FakeUser32:
    """Only GetAsyncKeyState, from a set of keys that are 'physically down'."""
    def __init__(self, down):
        self.down = down

    def GetAsyncKeyState(self, vk):
        return -32768 if vk in self.down else 0


def manager():
    fired = []
    m = hotkeys.HotkeyManager(on_activate=lambda kind, t: fired.append(("activate", kind)),
                              on_deactivate=lambda t: fired.append(("deactivate",)),
                              on_cancel=lambda: fired.append(("cancel",)),
                              on_other_key=lambda vk: None, is_recording=lambda: False)
    m.configure("ctrl+win", "esc", "both", 450, 300)
    return m, fired


def with_keys(down, fn):
    real_user32, real_dummy = hotkeys.user32, inject.send_dummy_key
    hotkeys.user32, inject.send_dummy_key = FakeUser32(down), lambda: None
    try:
        return fn()
    finally:
        hotkeys.user32, inject.send_dummy_key = real_user32, real_dummy


@case
def a_key_released_on_the_lock_screen_does_not_stay_held():
    m, fired = manager()
    down = set()

    def scenario():
        down.add(VK_LWIN)
        m._on_key(VK_LWIN, True, False, 1.0)              # Win+L: Win goes down here...
        down.discard(VK_LWIN)                             # ...and comes up on the lock screen (no event)
        down.add(VK_LCONTROL)
        m._on_key(VK_LCONTROL, True, False, 60.0)         # after unlocking: Ctrl alone
        return list(fired)

    assert with_keys(down, scenario) == [], fired         # before the fix: ctrl+win "pressed"


@case
def the_real_combination_still_fires():
    m, fired = manager()
    down = set()

    def scenario():
        down.add(VK_LCONTROL)
        m._on_key(VK_LCONTROL, True, False, 1.0)
        down.add(VK_LWIN)
        m._on_key(VK_LWIN, True, False, 1.05)
        return list(fired)

    assert with_keys(down, scenario) == [("activate", "press")], fired


class FakeClipboard:
    def __init__(self, text):
        self.text, self.seq, self.pasted = text, 1, []

    def get(self):
        return self.text

    def set(self, text, private=False):
        self.text, self.seq = text, self.seq + 1
        return True

    def ctrl_v(self, slow=False):
        self.pasted.append(self.text)


def with_clipboard(cb, fn):
    names = ("clipboard_get_text", "clipboard_set_text", "send_ctrl_v", "clipboard_has_non_text")
    saved = {n: getattr(inject, n) for n in names}
    saved_seq = inject.user32.GetClipboardSequenceNumber
    inject.clipboard_get_text, inject.clipboard_set_text, inject.send_ctrl_v = cb.get, cb.set, cb.ctrl_v
    inject.clipboard_has_non_text = lambda: False

    class Seq:
        def __call__(self):
            return cb.seq
    try:
        inject.user32.GetClipboardSequenceNumber = Seq()
    except Exception:
        pass
    try:
        return fn()
    finally:
        for n, f in saved.items():
            setattr(inject, n, f)
        inject.user32.GetClipboardSequenceNumber = saved_seq


@case
def back_to_back_pastes_keep_the_users_clipboard():
    cb = FakeClipboard("the user's own text")

    def scenario():
        inject.paste_text("first dictation", True, 150)
        time.sleep(0.05)
        inject.paste_text("second dictation", True, 150)  # before the first restore
        time.sleep(0.5)
    with_clipboard(cb, scenario)
    assert cb.pasted == ["first dictation", "second dictation"], cb.pasted
    assert cb.text == "the user's own text", cb.text      # before: the first dictation stayed for good


@case
def a_copy_after_the_paste_is_not_overwritten():
    cb = FakeClipboard("old")

    def scenario():
        inject.paste_text("dictation", True, 150)
        cb.set("what the user copied just now")
        time.sleep(0.4)
    with_clipboard(cb, scenario)
    assert cb.text == "what the user copied just now", cb.text


if __name__ == "__main__":
    run()
