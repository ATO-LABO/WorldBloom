# Story genre action scopes

Common actions are always present. `world.yaml.story_genres` contains an ordered
list of independent IDs, initially `combat` and `romance`. An empty list displays
as 共通. Worlds → world overview → advanced settings → ジャンル is the editing route.
The world list has no standalone genre tab. New manual and AI-created worlds start with that explicit empty list.
Existing worlds without this field retain their previous combat availability;
reading them never rewrites their files. Romance is always opt-in.

`engine/story_genres.json` is the application-maintained registry. Its entries
own stable IDs, names, descriptions, action membership, optional granted verbs,
and availability. World detail settings, validation and execution read
the same registry. There is no user-facing genre-creation feature.

To add a genre, add its definition and implement/test any new action handlers
and candidate conditions. `grant_actions` adds an implemented action to subjects'
available verbs; ordinary phase rules and target permissions still apply.
Names can be revised without changing saved IDs. Never reuse an ID for a
different meaning. Retire a genre with `enabled: false`, rather than deleting
its definition: it disappears from choices while its actions remain denied.
Unavailable IDs in existing files are ignored at runtime and preserved by
unrelated edits. Saving the genre selection intentionally replaces that list.
Restart the running process after updating the application registry.

All registered actions pass through the common execution gate, including forced
actions. Shared actions remain governed by the existing world rules. Changing
genres affects subsequent runs; saved execution history is not rewritten.

Romance currently offers `start_relationship` for a living co-located pair with
mutual stance at least 0.6. Affinity alone does not start dating. The chosen action
records both participants, dating state and start turn, and adds reciprocal phase
markers. Existing generic pledges remain separate. Relationship records appear in
simulation snapshots and the action outcome appears in the usual event log.
