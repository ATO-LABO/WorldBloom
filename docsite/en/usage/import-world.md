---
ja_rev: "2bfafad4efad"
---
# Import a World from ZIP

Import world settings as an **editable, reviewable draft**, rather than immediately creating a runnable world. ZIPs prepared with an AI tool can also be used.

## Entry point

From the world list, choose “新しい世界を作成” (Create a new world), then “ZIPから始める” (Start from ZIP). Select a file and click “ZIPから下書きを作る” (Create a draft from ZIP). Import does not modify the original ZIP or call AI.

## ZIP contents

Include exactly one `world.yaml` and `subjects/*.yaml` in the world folder. Related files and fields unsupported by the forms are preserved. A shared enclosing folder is supported. Unrelated files outside the world folder, unsafe paths, duplicate filenames, encrypted entries and symbolic links are rejected. Required template dependencies are checked as well.

For file-format background, see [`docs/world-import-guide.md`](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/world-import-guide.md). That document describes an older import flow; use the limits below for the current draft importer rather than its older capacity and screen instructions.

## Limits

- ZIP archive: up to 10MB.
- Archive entries, including directories: up to 1000.
- Extracted contents, including related files: up to 8MB.
- Parsed world/character YAML: up to 256KB per file.

## After import

Edit through the three-stage [Create a World](create-world.md) workflow. Original settings are preserved while supported fields are edited. Review the name, characters, places, time and rule support. Resolve invalid formats or missing dependencies according to validation messages.

At “③ 確認して採用” (Review and adopt), run “設定と実行入力を確認” (Check settings and execution inputs), including defaults and unsupported details. No new world is created until you check the confirmation and adopt. An adopted import draft becomes read-only; continue editing from the created world's settings. Import and adoption do not start GA.

## Errors or connection loss

Messages identify oversized archives, missing/multiple `world.yaml` files, duplicate names, entries outside the world folder and malformed YAML. Correct the issue before importing again. For uncertain submission outcomes, use the result-check action and inspect saved state before retrying, to avoid duplicate worlds.

## Not available in the read-only Viewer

Use writable Studio or a source server started with `--control`. Viewer cannot save or import worlds.
