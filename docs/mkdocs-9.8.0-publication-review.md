# MkDocs Material 9.8.0 publication review

Reviewed on 2026-10-30 for the duplicate dependency PRs #56 and #57.

The documentation publication failure was in the third-party JavaScript source
maps shipped by the pinned `mkdocs-material==9.8.0` package from PyPI. The only
email address in each map belongs to the upstream author and appears in the
public MIT copyright notices of the embedded TypeScript sources. It is the same
public attribution already reviewed for version 9.7.7. These maps contain no
WorldBloom runtime settings, user stories, credentials or unpublished contacts.

The review retains the existing content and credential checks. Only these exact
file contents are added to the existing email-only hash exceptions:

| Package path below `material/templates/` | SHA-256 |
| --- | --- |
| `assets/javascripts/bundle.c21346df.min.js.map` | `17168108f7ce26dfb278571f4db8dbafdb18ada6aadf54cba93fb629b73f9d48` |
| `assets/javascripts/workers/search.a7633da7.min.js.map` | `bcb8d79f2873cf66f17dcb6691f4ed3032dbf436a7b50339864da8910a55f694` |

No path, directory, author or domain is globally exempt. Changing a reviewed
map by even one byte requires review again. Gitleaks and all credential checks
remain enabled. The existing `lunr/tinyseg.js` content is unchanged.

Validation: strict Japanese and English site builds, publication checking of
the complete generated site, publication-guard regression tests, and direct
checks that the exact maps pass while modified bytes and private addresses
under the same filenames are rejected. The PR's current GitHub checks must
also pass before integration.
