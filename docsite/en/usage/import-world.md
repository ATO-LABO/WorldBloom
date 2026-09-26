---
ja_rev: "2ee7ce2ecbdb"
---
# Import a World from ZIP

You can import a world set up by an AI (Codex, Claude Code, Gemini, etc.) into
WorldBloom as a ZIP file. It's the third creation method on
[Create a World](create-world.md)'s "new world" screen.

## Entry point

From the home screen (**choose a world**), go to **＋ Create a new world** →
"**Import from ZIP**", and pick a ZIP file that bundles `world.yaml` and
`subjects/*.yaml`.

## What goes in the ZIP

The spec for building the ZIP lives at
[`docs/world-import-guide.md`](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/world-import-guide.md)
(GitHub). Hand that document to an AI tool as-is and ask it to follow it, and
it will produce a compatible ZIP.

## Limits

- 256KB per file
- 47KB for the ZIP itself
- 200 files at most
- 2MB total after decompression

Exceeding any of these fails at upload time.

## Name and genre

- **Display name**: if the form's "world name" field is left blank, the
  `name` from the ZIP's `world.yaml` is used. If you type something in the
  form, it overrides the ZIP's value.
- **Genre**: follows the ZIP's own specification. If `world.yaml` sets
  `gapengine` (`action_graph`/`effects`), the world is imported with that
  genre; if it's omitted, the world is imported **without a genre set**.

## After importing

The imported world takes you straight to its
[world settings screen](create-world.md#world-settings).

- **If the genre is unset**: pick one from the genre row on the "世界の概要"
  (overview) tab. **Do this before running anything.** A GA experiment will
  still run even if you don't -- picking a genre in the run settings is
  enough for that -- but this world's own foreshadowing
  (`gapengine.effects`) then stays silently empty for the whole run.
- Check the content via the run settings' "validate" step, or through each
  tab of the world settings screen.

## If you hit an error

Common messages and their causes (see chapter 8 of
[`docs/world-import-guide.md`](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/world-import-guide.md)
for the full list):

| Message | Cause |
|---|---|
| `ZIPにworld.yamlがありません` | `world.yaml` isn't at the ZIP's top level (or the top level of a single wrapping folder) |
| `<path>: 対象外のファイル名です` | a file under `subjects/` doesn't match the naming rule |
| `ZIPは47KB以内にしてください` | the ZIP itself is too large |
| `ZIPのジャンル「<id>」（templates/<id>/）がありません...` | the specified genre doesn't exist |
| `名前を入力してください（ZIPのnameも空です）` | both the form's display name and the ZIP's `world.yaml` name are blank |

## Not available in the read-only Viewer

The distributed **Viewer exe** (read-only viewer) rejects every write, so it
can't even open this screen. Import from the **Studio exe**, or from a server
started with `--control`.
