# FreeFlow

Private, local voice dictation for Windows, in the style of Wispr Flow.
Hold a hotkey, talk, release: the text is transcribed on your own GPU and typed
into whatever app has the cursor.

## Download

- **Full package (Windows 10/11, 64-bit, no install needed):**
  https://github.com/luizrodrigues987-source/FreeFlow/releases/latest - grab `FreeFlow-<version>-win64.zip`
  (about 1.2 GB), unzip anywhere, run `FreeFlow.exe`.
- **Already have FreeFlow?** It updates itself (tray > "Check for updates", `Update FreeFlow.bat` in its
  folder, or automatically at start-up). Very early packages without the self-updater: download
  https://github.com/luizrodrigues987-source/FreeFlow/releases/latest/download/Upgrade-FreeFlow.bat into the
  FreeFlow folder and double-click it; it fetches the current package (1.2 GB) and swaps it in.

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
  default) FreeFlow presses Enter to open the chat (so start with the chat box closed), types the text as
  Unicode characters, which reach text boxes only and can never trigger abilities, and leaves you to press
  Enter to send; list the game under "send after" as well to send automatically. "Insert by" offers a slow
  Ctrl+V chord or real keystrokes for games that ignore Unicode input. Chat messages are one line: line
  breaks become spaces. Games are never asked accessibility
  questions (a League match once kept FreeFlow waiting 30 s for an answer), and any other window gets half
  a second to describe its focused control before the text simply stays where you are. For games that
  only react to real keystrokes, add the exe under "Always type in" and FreeFlow types with hardware scan codes. A game running
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

The packaged FreeFlow updates itself from the GitHub release: at start-up it downloads the small
`FreeFlow-update-latest.zip` (about 70 KB), and when that code is newer than what is installed it stages
it in `%APPDATA%/FreeFlow/update` and restarts. Friends can also double-click `Update FreeFlow.bat` in
their FreeFlow folder, use the tray menu's "Check for updates", or the button in Settings > About
(where the automatic check can be switched off). A broken update is set aside automatically and the
built-in code runs.

`Upgrade-FreeFlow.bat` (a 5 KB batch file, also a release asset) upgrades *any* installation, including
the first packages that cannot update themselves: it finds the install (folder, running process or the
start-up entry), downloads the latest full package from GitHub, mirrors it into place and restarts.

To publish an update: run `build_update.bat`, then upload the zip to the release twice (its revisioned
name and `FreeFlow-update-latest.zip`), e.g.
`gh release upload v1.1.0 dist\FreeFlow-update-r<rev>.zip dist\FreeFlow-update-latest.zip --clobber`.
Updates only carry FreeFlow's own code: a new third-party dependency or a new version number needs a
full package (`build_exe.bat`, then `gh release create v<ver> ...`); the app tells users when that is
the case and links to the release page.

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
- The keyboard hook runs on a high-priority thread and re-installs itself when Windows called it late
  (a busy game can make Windows drop slow hooks without notice); such late calls are logged as warnings,
  and so is any dictation start that took longer than 0.4 s, with a breakdown of where the time went.
