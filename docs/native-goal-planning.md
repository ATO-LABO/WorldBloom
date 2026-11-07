# Native actions in shared goal planning

## Scope

`native_planning.version: 1` exposes existing ordinary verbs to `GoalPlanner`.
It adds operators, not another search engine or a story-name dispatch table.
Original `VerbEngine` handlers still execute gathering, crafting, observation,
modifier removal, item gifts, persuasion, contests and evidence review.
Response operators and existing negotiation/consent remain available alongside
native operators. Calendar task/promise operators keep their existing adapter.

The adapter currently accepts ordinary slot execution without `action_timing`.
It rejects minute/calendar mode and timed native actions rather than predicting
one-slot effects for actions with a different execution model. It does not expose
every native verb: labor, sabotage, sacrifice, planting, rituals and donation
have not been added as causal operators in this change.

## Definitions and receipts

A world declares bounded `outcomes` with `key`, `verb`, `match`, and `value`.
The key must exist in response state; supported completion events are `fight`
and `confront`. Matching may use actor, target, result, winner, loser or fact.
Only an actual matching handler result updates the milestone. A forecast win,
an offered exchange or an accusation never becomes an actual accomplishment.

A milestone receipt reaches people physically present at the completed action.
Remote characters retain their last received value. Remote position and strength
beliefs are not refreshed from hidden truth. Seeing that a remembered person
is absent clears that presence; the actor can search accessible places instead.
Projected route access and phase predicates use received coordinates. Unknown
phase inputs stay unprojected. Borrowed transport requires the remembered lender
to be present; remote actual ownership never authorizes travel.
Evidence sources expose where to investigate, not which suspect is actually
guilty. Evidence is learned and reconsidered by the existing handlers.

Boolean knowledge prerequisites for recipes are searched as well as materials.
Search does not add that knowledge to the actual character. Materials are
consumed only when crafting actually executes. Only an observed item modifier
with known held stock predicts a loot transfer; a non-item modifier with the
same source name cannot create an item. Crossed phases persist in
projected travel so a later projected return does not re-enable forbidden verbs.

## Uncertainty and constraints

A contest step carries a conditional success probability calculated from the
actor's received information. Execution samples the existing contest and can
produce a different result. Loss and a mistaken accusation trigger replanning.

The adapter's current risk policy permits a contest when its estimated chance
is at least `0.1 * (1 - temper)`. This is an explicit provisional policy using
an existing trait, not a new personality parameter or a guarantee of victory.
Known losing branches that discard lootable possessions or down the actor are
checked against required/higher-priority commitments before offering a fight.
This does not claim exhaustive modeling of every delayed or unknown harm.

Relationship forecasts honor observed caps. When an attempted improvement has
no effect, that observation can motivate investigating the other person's
existing modifiers. Their discovery and removal still require existing native
permissions. A romance in detective grammar cannot silently unlock forbidden
verbs. This preserves the source model; it does not introduce a consent model
for romance or claim that removing a modifier is realistic interpersonal advice.

Bounded heuristic search prioritizes unresolved transport/material prerequisites
and known destinations. It is not an optimal planner. It may stop with a partial
information path, `search_limit` or `no_known_plan`. None means the author's
final goal has already failed. Goal rules retain achievement, failure and period
judgment; `objective_achieved(condition)` reads the actual recorded verdict.

## Safe migration and tests

`tools/migrate_legacy_stories.py` creates separately named editions of the four
Momotaro worlds, romance, detective and the romance/detective grammar comparison.
Each edition retains the full cast, traits, inventory, native mechanics and
source hashes. Only its protagonist is moved to common automatic planning;
supporting characters retain their original decisions. The changed assumptions
are listed in each `migration.json`, including initial knowledge, public
relationship scores, removal of forced departure and the added actual victory
requirement. Failure before the deadline is not invented where the source has
no authored irreversible failure condition; deadline failure still applies.

Existing editions with edits are never overwritten by regeneration. Tests build
temporary editions from the original worlds, so engine tests also run before the
shipped example editions are added. Full old calendar Melos and the older
multi-day festival are outside this ordinary-action migration. Their existing
editions and histories are preserved.

Run `python -m unittest discover -s tests -p test_native_planning.py` and the
existing goal, calendar, objective, engine and planning-view suites. These are
behavior checks, not proof of literary quality or of GA search performance.
