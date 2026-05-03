# Whisperer Mac

Whisperer Mac is the macOS port of the Whisperer desktop dictation app. It keeps
the Windows version's PyQt launcher, persistent Python engine process, embedded
React dashboard, native overlay, modes, vocabulary, history, file transcription,
last-dictation recovery, local/cloud STT providers, and paste controls.

## Quick Start

```bash
cd "/Users/kynewman/Desktop/Whisperer Mac"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-macos.txt
npm --prefix whisperer-app install
npm --prefix whisperer-app run build
python launcher.py
```

If this Mac has Node.js but no npm, `./build_macos.sh` uses the checked-in
`package-lock.json` to install the dashboard packages before building.

You can also double-click `Launch Whisperer.command` after the virtual
environment and dashboard are built.

macOS will ask for permissions the first time the app uses native integration:

- Microphone, for dictation audio.
- Accessibility, for global hotkeys and paste/active-app context.
- Screen Recording, only if OCR context capture is enabled.

## Defaults

- Dictation hotkey: `Ctrl + Cmd` hold to dictate.
- Long-form lock: hold the dictation hotkey and press `Option`.
- Default dictation mode: Groq Whisper Large v3 Turbo when a Groq API key is saved, with local Whisper fallback.
- Default local fallback model: Whisper v3 Turbo through faster-whisper.
- Model cache: `models/` in source, or `~/Library/Application Support/Whisperer/models` in packaged builds.
- User data: `~/Library/Application Support/Whisperer`.

## Fast Cloud Transcription

Whisperer uses Groq's OpenAI-compatible Whisper endpoint for the built-in
`Voice` and `Fast Cloud` modes. Add a Groq key in Configuration -> API keys,
then use Test to verify it. If no Groq key is saved, or the cloud request fails,
dictation automatically falls back to the selected local Whisper model.

## Build

```bash
./build_macos.sh
```

Output:

- `dist/Whisperer.app`
- `dist/Whisperer-macOS-arm64.dmg`

The build script creates `.venv`, installs macOS dependencies, builds the React
dashboard into `whisperer-app/dist`, then packages the PyQt shell with
`whisperer-macos.spec` and creates a drag-to-Applications DMG.

To rebuild only the installer from an existing app bundle:

```bash
scripts/create_macos_dmg.sh
```

GitHub source cannot include the DMG directly because the packaged app is larger
than GitHub's per-file limit. Publish `dist/Whisperer-macOS-arm64.dmg` as a
GitHub Release asset for user-friendly installation.

## Project Structure

```text
core/                 Engine helpers: audio, STT, settings, modes, history, output
rules/                App-specific formatting rules
scripts/              Diagnostics and startup helpers
ui/main_window.py     PyQt host for the embedded React dashboard
ui/overlay.py         Dictation overlay pill
ui/tray.py            System tray integration
whisperer-app/src/    React dashboard source
whisperer-app/dist/   Built dashboard loaded by PyQt
launcher.py           UI launcher and single-instance entrypoint
main.py               Engine process entrypoint
whisperer-macos.spec  PyInstaller macOS app recipe
```

## Notes

- `models/`, `build/`, `dist/`, `node_modules/`, and local databases are ignored.
- Parakeet/NeMo is not included in the standard macOS dependency set; the
  supported local macOS path is faster-whisper for stability.
- Tesseract is optional for OCR context. Install it with Homebrew if you want
  OCR: `brew install tesseract`.
