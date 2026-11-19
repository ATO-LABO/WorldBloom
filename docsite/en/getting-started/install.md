---
ja_rev: "012bd8d3858f"
---

# Install

!!! note "The app's screens are Japanese-only"
    WorldBloom's UI is not translated yet. Wherever this site names a button or heading, it gives the Japanese label with an English gloss in parentheses, e.g. "生成を開始 (Start generation)", so you can find it on screen.

## Requirements

- Windows 10/11, WebView2 (already present on most systems)
- Studio needs **Python 3.12+** on PATH (`pip install pyyaml`); Viewer does not
- git is optional (without it, some provenance info in run output is simply left blank)

!!! tip "AI features need a configured backend"
    The base GA simulation runs without an LLM, except when you enable the optional rationality layer. AI-assisted world creation and refinement, synopsis generation, and prose generation need a backend and model. See **⚙ 全体設定 (Global settings)** and [LLM Backends](../usage/llm-backends.md) for the current screen. Ollama and model weights are not bundled. Configurations that use an external service send the generation input to that service.

=== "Studio (full-featured)"

    Download [WorldBloom-Studio-portable.zip](https://github.com/ATO-LABO/WorldBloom/releases/latest/download/WorldBloom-Studio-portable.zip), extract it, and double-click `WorldBloom-Studio.exe`.

    - Pick a world, run a GA experiment, do Sifting, and generate synopses/text, all from this app
    - On first launch, `runs/` and `control/` folders are created next to the exe automatically
    - Do not move the `app/` folder inside the extracted directory — it holds the application itself
    - Without Python 3.12+ on PATH (with `pip install pyyaml` done), Studio shows an error screen and does not start

=== "Viewer (read-only)"

    Download [WorldBloom-portable.zip](https://github.com/ATO-LABO/WorldBloom/releases/latest/download/WorldBloom-portable.zip), extract it, and double-click `WorldBloom.exe`.

    - Browse three pre-generated sample experiments (Momotaro, romance, detective) — grids, synopses, and text — with no setup
    - No Python or Ollama install needed
    - This build is read-only: clicking the on-screen selection checkboxes etc. returns a 403 error, which is expected

=== "From source"

    Requirements:

    - Python 3.12+ (regression-tested on 3.12/3.13)
    - `pip install -r requirements.txt` (PyYAML only)

    ```
    git clone https://github.com/ATO-LABO/WorldBloom.git
    cd WorldBloom
    pip install -r requirements.txt
    ```

    Open the bundled `samples/` (three sample experiments). Use a copy if you need to preserve the originals, because selection controls can write to these files:

    ```
    python viewer/server.py --runs samples --port 5401
    ```

    Open `http://127.0.0.1:5401/`. This mode has no run controls or config editing, but Sifting's selection checkboxes still write — directly to each experiment's `selection.json` under `runs`, bypassing job management (no `--control` flag).

    To run your own GA experiments or regenerate synopses/text, start with `--control` (keep `runs`/`control` outside the repository):

    ```
    python viewer/server.py --runs <path to runs> --control <path to control> --port 5401
    ```

    For running experiments and generation directly from the command line, see [CLI Workflow](../usage/cli-workflow.md).

## Source platforms and saved data

Run management is supported on Windows and Linux. Linux additionally requires Linux 5.3+ pidfds, child-process supervision support, and `/proc` in the same PID namespace. Jobs are refused when those facilities are unavailable. The Windows EXE and source-based Linux setup are separate installation routes.

Only new-format worlds containing `world_format_version: 1` can be edited and run by the current source. Use the bundled 桃太郎＋3 (Momotaro +3, `projects/momotaro_plus3_new`) or a world confirmed through the creation screen. Do not overwrite legacy worlds or run outputs; retain the original code and Python environment separately when needed. Adding a version number by hand is not a format conversion.

## If Windows SmartScreen warns you

Unsigned builds may trigger Windows SmartScreen on first launch. Verify that the download came from this repository’s official Release and, where published, that its checksum matches. Do not run files from an unknown source or with a mismatched checksum, and do not bypass warnings automatically.

## Where the downloads actually live

Distributed builds are on [GitHub Releases](https://github.com/ATO-LABO/WorldBloom/releases). Check each Release for the attached ZIP versions, changes, and requirements. Updating the source does not rebuild or update the EXE downloads.
