# Shared WorldBloom viewer design

New screens use the shared design by default. A different design requires an
explicit user request or a documented content-specific need (for example, a
story-reading layout). Every viewer layout carries the `ui-screen` baseline.

## Ownership and loading

`viewer/static/ui-tokens.css` owns the `--ws-*` values. They preserve the existing
world-detail palette and sizing, with shared desktop content insets of 16px / 28px.
`viewer/static/ui.css` owns default pages, layout primitives and reusable controls.
`viewer/ui.py` owns escaped markup helpers. Text and attribute values are escaped;
HTML slots such as heading actions and footer contents accept renderer-built HTML
only. Never pass raw user/model HTML into those slots.

`pages.document()` loads tokens, legacy app/purpose styles, optional page styles,
then shared components. Use `stylesheets=` / `scripts=` for new page assets. Their
names must be local static filenames and must be registered in `viewer/server.py`.
Do not append an override stylesheet by replacing `</head>`.

The default `standard-workspace` already styles headings, body content and native
controls. Use `form-workspace` for custom form screens, with a `ui-workspace
ui-workspace--form` content root. For a left navigation pane, use
`ui-workspace ui-workspace--nav` inside `settings-workspace`.

All viewer renderers, including read-only, comparison, lineage, generation,
fact and story screens, register assets through the renderer. Shared styles load
last. `review_pages.doc()` accepts additional stylesheets/scripts and keeps that
order. Existing CSS class names remain as compatibility aliases so the same
headings, form controls, side navigation and action bars have one shared owner.
Do not add another page-specific copy of those component definitions.

## Components

The top bar uses one read-only purpose context on every screen. The initial
landing page shows "未選択"; after the initial choice, the current purpose is
shown as text. Do not add a header selector or a mid-workflow purpose switch.
The header has no auxiliary purpose-workspace link. Initial purpose choices
lead directly to `/worlds?mode=create` or `/worlds?mode=review`; use the normal
world-selection and phase navigation instead of adding a second entry hub.
Keep the chosen mode in navigation URLs and keep the current page's purpose
when restoring from the browser back/forward cache. Header typography and
responsive arrangement belong to `ui.css`, never a screen-specific stylesheet.

- `ui.heading()` and `.ui-heading`: title and supporting text.
- `ui.field()` and `.ui-field`: associated label, required badge and control.
- `.ui-input-group`: input with an inline unit.
- `ui.button()`: secondary, advance (orange), confirm/save (green).
- `ui.navigation()` and `.ui-nav`: shared navigation appearance.
- `ui.footer()` and `.ui-footer`: persistent bottom actions within a flex workspace.
- `.ui-content`, `.ui-split`, `.ui-aside`: scrollable content and optional summary.
- `.ui-notice`, `.ui-notice--warning`, `.ui-error`, `.ui-hint`: status and help.
- `.ui-section-head`: section title with an edit action.
- `.ui-edit-dialog` and `.ui-edit-dialog-actions`: native edit dialog and sticky
  save/cancel actions. Saved-world `.wp-dialog` uses the same shared owner.
  Dialog widths, text field heights, backdrop and shadow are shared tokens.
  Restore focus to the edit button on close, keep text literal, and preserve the
  original value on cancellation. Draft dialog saves update the local proposal;
  they do not publish a world or call a model. Revalidation is a separate action.

The default type scale is 24 / 20 / 17px for headings, 16px body, 15px controls,
14px supporting text and 12px metadata. The base control height is 44px and corner
radius 6px. Use tokens, not copied literal values. The system uses installed font
fallbacks; it does not bundle a font or promise identical glyphs across devices.

Dense analysis toolbars use the explicit 34px compact control size. Editable
forms use 44px controls; inputs inside a bordered unit group use 42px so the
whole group stays 44px. Chart labels can retain coordinate-dependent sizes;
metric emphasis uses display tokens. Monospace editors use the code token and
story prose uses the shared reading line height. Reader font-size controls and
categorical graph colors remain content-specific.

The desktop startup-error window inlines the same tokens/components because its
HTTP server may not be available. If the CSS files themselves cannot be read,
the escaped error text still appears without a second hard-coded visual theme.

## Minimal new screen

```python
from viewer import pages, ui

def render():
    body = ui.field('Title', '<input name="title" required>', required=True)
    body += ui.button('Save', kind='confirm', attrs={'type':'submit'})
    return pages.document('New screen', '<form>' + body + '</form>')
```

This requires no dedicated stylesheet. For structured screens, combine the same
components rather than reimplementing controls. Preserve the semantic `<form>`
and native input types. External submit buttons must have the matching `form`
attribute. Actions that submit must use `type=submit`; other actions use
`type=button`. Keep button focus and labels available when inputs are disabled.

## Responsive behavior

Shared content insets change at 760px. Side summaries collapse below content and
navigation wraps; the footer can wrap its message above actions. Do not hide the
main submit/save action on narrow screens or introduce whole-page clipping.
Long editor content scrolls while actions stay available. Tables may transform
into labelled rows on narrow screens. Inputs, status text and errors must wrap
without increasing viewport width.

All 23 stylesheets were audited, including the original app stylesheet. Legacy
palette and spacing variables now resolve to shared tokens. Screen headings,
ordinary form controls, content insets, focus indicators, cards and action bars
use the same scale. Graph geometry, plot labels, data colors and prose reading
layouts remain scoped. On narrow screens, creation/editing workspaces keep save
actions outside their scrolling fields; long read-only condition and record
pages can scroll as a document.

## Checks and intentional exceptions

Run `node tests/ui_design_contract.cjs` (also automatically included by
`node scripts/test_javascript.cjs`) and `test_ui_design.py` plus relevant behavior
tests. The contract rejects additional token owners and literal typography,
spacing or color declarations in new/migrated page CSS. Purpose, fact, story and
route-goal styles have been removed from the legacy exception list. Literal UI
font sizes are rejected across the remaining stylesheets too; SVG/plot labels
are explicit coordinate-system exceptions. The remaining legacy list covers
domain layout and diagram rules, not a template for new screens. Graph dimensions,
scroll bounds, percentages and necessary one-pixel borders are not typography
or basic-spacing exceptions.

Browser checks compare baseline world detail, AI/manual creation and settings at
desktop and 390px widths. Measure fonts, spacing and controls; inspect screenshots
for hierarchy, footer clipping and overflow. Exercise submit/review/cancel/adopt
with fixed responses, manual creation in a temporary repository, settings tabs
and save/reset using fake local services. Never generate with a real provider or
edit production credentials just to verify design.

Recorded planning uses a dedicated, optional candidate tab. Its content-specific
layout has an actor/objective selector, author-facing final verdict, milestone
list and selected historical scene. Desktop comparisons use two task columns;
narrow layouts stack them and keep timeline scrolling local. Candidate metrics
are hidden while this full-width tab is active. This is an information-layout
choice, not another style/token owner. Old per-decision planning disclosures
start collapsed. Selection and route expansion survive tab switches; switching
actor/objective retains that group's selected scene independently.

Timed activity records appear in the same planning workspace. Selection and
execution are distinct source records. Shared notices show start/expected finish
using authored day/slot labels, actual elapsed effort and explicit ongoing,
completed, failed, interrupted or unfinished status. Starting is never labelled
arrival or success. The view consumes activity events, not new actor decisions.

Native calendar goal choices use that same workspace. The reader preserves their
original event/decision source lines and distinguishes journaled selection from
native completion. Rest completion is a visible milestone with actual recovered
stamina; date/time labels come from the execution calendar, not inferred slots.
