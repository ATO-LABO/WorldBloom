# Viewer design defaults

- Use the shared WorldBloom design unless the user's instructions explicitly request a different design. The reference is the world-detail workspace: green top bar, phase band, restrained typography, separators, and persistent bottom actions.
- Read `docs/ui-design.md` before adding or changing a viewer screen.
- Use `pages.document()` with its default `standard-workspace`, or the shared `form-workspace` / `settings-workspace` layouts. Register page assets through `stylesheets=` and `scripts=`; do not splice stylesheets into `</head>` in new screens.
- Reuse `viewer.ui` helpers and `ui-*` components for headings, fields, notices, buttons, navigation and footers. Put content-specific data hooks on the existing controls so behaviors remain stable.
- `viewer/static/ui-tokens.css` owns all `--ws-*` values. Page CSS must reference those values for typography, spacing, control appearance and colors. Do not copy shared component definitions into a page stylesheet.
- Different column arrangements, graph geometry and reading layouts are allowed when the task requires them; keep the shared visual rules. Record intentional design exceptions in the UI guide and in the contract checker when required.
- Run the shared-design contract, relevant Python/JavaScript tests and browser checks at desktop and narrow widths. Check scrolling, bottom actions, focus visibility, error/disabled states and preserved values. Do not replace functional checks with screenshots.
- Existing legacy viewer styles are migration exceptions, not templates for new screens. Do not expand the legacy exception list just to make a new screen pass.
- Preserve saved worlds, settings and histories. Keep standard CI environments and protections intact.
