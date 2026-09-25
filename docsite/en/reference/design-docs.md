---
ja_rev: "dd0e27c02945"
---
# Design Docs

This documentation site explains "how it works and how to use it, today." The **design history, decisions, and implementation plans**, by contrast, live in the design docs under `docs/`. Check there when you want the background behind a spec — why it ended up this way, what alternatives were considered and rejected.

## Detailed design

- [`docs/2026-09-11_gapengine-detailed-design.md`](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/2026-09-11_gapengine-detailed-design.md) — GapEngine's detailed design. The source of truth behind this site's [Concepts](../concepts/seven-layers.md) chapter: the seven layers, the genome, action types, foreshadowing, fixed endings, the QD map, coevolution, and more.

## Viewer UI/UX design

- [`docs/2026-09-12_viewer-ux-design.md`](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/2026-09-12_viewer-ux-design.md) — Design of the viewer's screen layout and operating flow. Written back on 2026-09-12, so some of it no longer matches the current screens after later UI overhauls (the world-page redesign, the run-tab redesign, etc.). See [Usage](../usage/create-world.md) for how the screens actually work today.

## For developers

- [`docs/DEVELOPMENT.md`](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/DEVELOPMENT.md) — Developer-facing info: how to run the regression tests, the determinism guarantee, the exe build steps, how to update the docs, and more.

## Implementation plans and per-feature design records

`docs/` also holds phase-by-phase implementation plans and design/verification records for individual features. The older the date, the more likely it no longer matches the current state after subsequent changes.

| File | Contents |
|---|---|
| `2026-09-11_phase0-implementation-plan.md` | Phase 0: rebuilding the core engine + GA × QD plumbing |
| `2026-09-11_phase1-implementation-plan.md` | Phase 1: completing the perception layer, weakening, the three principles |
| `2026-09-11_phase2-implementation-plan.md` | Phase 2: delayed effects, status, ritual, modifier rules |
| `2026-09-11_phase3-implementation-plan.md` | Phase 3: coevolution, the output stage (synopsis → human selection → full text), the viewer |
| `2026-09-11_phase4-implementation-plan.md` | Phase 4: porting/adapting to other genres (rethink), groundwork for meta-evolution |
| `2026-09-12_viewer-phase1-implementation-plan.md` | Viewer phase 1, "make it readable" implementation plan |
| `2026-09-12_explain-*.md` (4 files) | Records of the "choice, rationale, cost, turning point" display contract, implementation plan, handover, and reader verification |
| `2026-09-18_llama-server-backend-plan.md` | Plan for adding the llama-server backend (Bonsai 2 27B) |
| `2026-09-18_gpu-guard-plan.md` | Plan for GPU Guard (server auto-start, GPU coordination, thermal guard) |
| `2026-09-20_screening-workspace-concept.md` / `-validation.md` | Improvement proposal and implementation/verification records for the narration screen |
| `2026-09-20_sifting-workspace-design.md` / `-validation.md` | Design and implementation verification record for the Sifting workspace |
| `2026-09-20_synopsis-modal-design.md` | Design of the synopsis modal and world diff |

For the always-current listing, check the [`docs/` directory on GitHub](https://github.com/ATO-LABO/WorldBloom/tree/main/docs).
