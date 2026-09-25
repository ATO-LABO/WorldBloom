---
ja_rev: "0d8bf13aa19e"
---

# Install

!!! note "The app's screens are Japanese-only"
    WorldBloom's UI is not translated yet. Wherever this site names a button or heading, it gives the Japanese label with an English gloss in parentheses, e.g. "生成を開始 (Start generation)", so you can find it on screen.

## Requirements

- Windows 10/11, WebView2 (already present on most systems)
- Studio needs **Python 3.11+** on PATH (`pip install pyyaml`); Viewer does not
- git is optional (without it, some provenance info in run output is simply left blank)

!!! tip "Pick your LLM backend in ⚙ 全体設定 (Global settings) first"
    A GA experiment itself does not need an LLM at all — only synopsis/text generation does. If you plan to use generation, open the app's **⚙ 全体設定 (Global settings)** screen first and choose a backend (Ollama / llama-server / claude-cli / codex-cli / Anthropic API / OpenAI API / 生成しない (no generation; save prompts only)) and a model name. With no settings at all, the default backend is llama-server (codex-cli in the v1.0.0-viewer release). Only claude-cli works with no model set (it falls back to `claude-sonnet-5`); every other backend requires you to set one yourself. Ollama and llama-server themselves are not bundled with WorldBloom; install them separately. See [LLM Backends](../usage/llm-backends.md).

=== "Studio (full-featured)"

    Download [WorldBloom-Studio-portable.zip](https://github.com/ATO-LABO/WorldBloom/releases/latest/download/WorldBloom-Studio-portable.zip), extract it, and double-click `WorldBloom-Studio.exe`.

    - Pick a world, run a GA experiment, do Sifting, and generate synopses/text, all from this app
    - On first launch, `runs/` and `control/` folders are created next to the exe automatically
    - Do not move the `app/` folder inside the extracted directory — it holds the application itself
    - Without Python 3.11+ on PATH (with `pip install pyyaml` done), Studio shows an error screen and does not start

=== "Viewer (read-only)"

    Download [WorldBloom-portable.zip](https://github.com/ATO-LABO/WorldBloom/releases/latest/download/WorldBloom-portable.zip), extract it, and double-click `WorldBloom.exe`.

    - Browse three pre-generated sample experiments (Momotaro, romance, detective) — grids, synopses, and text — with no setup
    - No Python or Ollama install needed
    - This build is read-only: clicking the on-screen selection checkboxes etc. returns a 403 error, which is expected

=== "From source"

    Requirements:

    - Python 3.11+ (developed on 3.13)
    - `pip install -r requirements.txt` (PyYAML only)

    ```
    git clone https://github.com/ATO-LABO/WorldBloom.git
    cd WorldBloom
    pip install -r requirements.txt
    ```

    Open the bundled `samples/` (three sample experiments) read-only:

    ```
    python viewer/server.py --runs samples --port 5401
    ```

    Open `http://127.0.0.1:5401/`. This mode has no run controls or config editing, but Sifting's selection checkboxes still write — directly to each experiment's `selection.json` under `runs`, bypassing job management (no `--control` flag).

    To run your own GA experiments or regenerate synopses/text, start with `--control` (keep `runs`/`control` outside the repository):

    ```
    python viewer/server.py --runs <path to runs> --control <path to control> --port 5401
    ```

    For running experiments and generation directly from the command line, see [CLI Workflow](../usage/cli-workflow.md).

## If Windows SmartScreen warns you

The distributed exe files are unsigned, so SmartScreen may warn on first launch. Click "More info" then "Run anyway".

## Where the downloads actually live

Distributed builds are on [GitHub Releases](https://github.com/ATO-LABO/WorldBloom/releases). As of this writing the latest tag is `v1.0.0-viewer`, with two attached files: `WorldBloom-portable.zip` (Viewer) and `WorldBloom-Studio-portable.zip` (Studio).
