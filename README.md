# FreeFlow

Private, local voice dictation for Windows, in the style of Wispr Flow.
Hold a hotkey, talk, release: the text is transcribed on your own GPU and typed
into whatever app has the cursor.

## Download

- **Full package (Windows 10/11, 64-bit, no install needed):**
  https://github.com/luizrodrigues987-source/FreeFlow/releases/latest - grab `FreeFlow-<version>-win64.zip`
  (about 1.2 GB), unzip anywhere, run `FreeFlow.exe`.
- **Code-only update for an existing installation (about 70 KB):**
  https://github.com/luizrodrigues987-source/FreeFlow/releases/latest/download/FreeFlow-update-latest.zip -
  unzip and double-click `Update FreeFlow.bat`.

## What it does

- **Hotkey** (default `Ctrl + Win`). Hold it and speak; release to insert the text. **Double-tap** it for
  hands-free dictation; a single press (or `Esc`) ends it. Normal shortcuts such as Ctrl+C / Ctrl+V and
  Ctrl+Win+Arrow are not affected. Settings > General also offers hold-only and double-tap-only modes.
- Soft chimes mark the start and end of a dictation (volume adjustable in Settings).
- **Smart target**: the text goes to the window you started dictating in, even if you click elsewhere
  while talking. If the window in front clearly cannot take text (the desktop, a file list, a web page
  without a focused field, a button), the text goes to the last window you dictated into, which is brought
  to the front; the indicator shows a "-> Claude" style hint. Windows that give no accessibility
  information (games, chat apps with the focus on a pane) always keep the text.
- **Games**: for games listed under Settings > Formatting > "Game chat: open first" (League of Legends by
  default) FreeFlow presses Enter to open the chat, pastes the text, and leaves you to press Enter to send;
  list the game under "send after" as well to send automatically. For games that only react to real
  keystrokes, add the exe under "Always type in" and FreeFlow types with hardware scan codes. A game running
  as administrator hides its keystrokes from FreeFlow unless FreeFlow runs as administrator too; the
  indicator is not visible over exclusive-fullscreen games.
- Settings are saved when you click Apply or close the window.
- **Local transcription** with faster-whisper (`large-v3-turbo` on the RTX 3070, about 0.3 s per sentence).
  Optional cloud engines (OpenAI, Groq) if you ever want them.
- **Mutes your speakers while you dictate** and unmutes them when you stop. Skips the mute automatically
  when the default output looks like headphones or a headset (Arctis, etc.).
- **Mutes your microphone per app** while you dictate: Discord (and any other program you list) receives
  silence during the dictation and gets its microphone back afterwards. Nothing else on the PC is affected.
- **Clean-up**: removes "um / uh", supports "new line" / "new paragraph" / "bullet point", custom
  replacements (e.g. "my email" -> your address), vocabulary hints for names.
- **Automatic lists**: "I need three changes. One, ... Two, ... Three, ..." (or "first ... second ...",
  "number one ...") becomes an intro line plus a numbered list. Un-numbered enumerations that are clearly
  a list ("a few things: ..., then ..., and also ...") are turned into bullet points by the local model.
- **Sentence structuring** (like Wispr Flow's auto-edits): a small language model running locally through
  Ollama (Qwen 2.5 3B by default, Gemma 3 4B also installed) turns run-on speech into punctuated sentences
  and paragraphs and drops repeated words and false starts, in well under a second on the GPU.
  On by default when Ollama is installed ("Auto"); Claude or OpenAI can be used instead with an API key.
- **Island indicator**: a small round-ended pill sits at the bottom centre of the screen all the time and
  expands with a short animation while you dictate (bars that swing with your voice thanks to automatic
  gain, then dots while transcribing, then a check mark; purple bars mean hands-free). Click it to start
  or stop hands-free dictation, right-click for Settings. Settings > General can switch it to "only
  while dictating".
- Tray icon, history, start-with-Windows, crash-safe unmuting.

## Running it

- FreeFlow starts with Windows and runs silently in the background (purple mic icon in the tray).
  Use the **FreeFlow** desktop shortcut or `FreeFlow.vbs` to start it by hand; neither opens a window.
- `FreeFlow.bat` does the same but flashes a console for a moment. `Troubleshoot (console).bat` keeps a
  console open with verbose logging; only use it when something is wrong.
- Double-click the tray icon (or right-click -> Settings) to change the hotkey, muting rules, model, etc.
- Command line switches (e.g. `wscript FreeFlow.vbs --restart`): `--quit`, `--restart` (clean restart,
  useful after editing the code), `--autostart on|off`.

Quit the original Wispr Flow while using FreeFlow: both react to `Ctrl + Win`.

## Sharing it (standalone package)

`build_exe.bat` (or `.venv\Scripts\python.exe build_exe.py`) builds `dist\FreeFlow\FreeFlow.exe`, a
self-contained copy that needs no Python on the other PC, and zips it as `dist\FreeFlow-<version>-win64.zip`
(also copied to the Desktop). The zip includes the CUDA libraries (about 1.5 GB unpacked), a
`README-FreeFlow.txt` for the recipient and the source code. Add `--cpu-only` for a much smaller package
without GPU support. The recipient unzips it anywhere, runs `FreeFlow.exe`, and installs Ollama separately
if they want sentence structuring.

## Updating friends' installations

`build_update.py` (venv) makes `dist/FreeFlow-update-r<revision>.zip`, a code-only update of about 100 KB
(also copied to the Desktop) that is small enough to send over Discord. The recipient unzips it and
double-clicks `Update FreeFlow.bat`: the code goes to `%APPDATA%/FreeFlow/update` and FreeFlow restarts.
`FreeFlow.exe` uses that folder whenever its revision is newer than the bundled code, and falls back to
the bundled code if an update fails to load. Updates only carry FreeFlow's own code; when a new
third-party dependency is added, rebuild and resend the full package. Packages built before this
mechanism existed do not look for updates.

## Setup on another PC

Run `setup.bat` (needs Python 3.10+). It creates `.venv` and installs everything from `requirements.txt`,
including the CUDA libraries. The speech model (about 1.6 GB) is downloaded automatically on first use.

## Files

| Path | Purpose |
|------|---------|
| `freeflow/app.py` | main state machine (hotkey -> record -> mute -> transcribe -> type) |
| `freeflow/hotkeys.py` | low-level keyboard hook, hotkey parsing |
| `freeflow/audioctl.py` | speaker mute + per-app microphone mute (Windows Core Audio) |
| `freeflow/recorder.py` | microphone capture |
| `freeflow/transcribe.py` | faster-whisper / OpenAI / Groq engines |
| `freeflow/postprocess.py` | filler removal, voice commands, replacements |
| `freeflow/llm.py` | optional Claude / OpenAI polish |
| `freeflow/inject.py` | clipboard paste or keystroke typing into the focused app |
| `freeflow/overlay.py`, `tray.py`, `settings_ui.py` | UI |
| `%APPDATA%\FreeFlow\` | `config.json`, `history.jsonl`, `freeflow.log` |

## Notes

- Muting works through the Windows audio session API, the same mechanism as the Volume Mixer. Windows
  remembers per-app mute states, so FreeFlow records what it muted and restores it even after a crash
  (`mute_state.json`).
- The speaker mute only touches the *default* playback device, so virtual cables and voice changers
  keep working.
- Everything runs locally unless you switch on a cloud engine or AI polish in Settings.
