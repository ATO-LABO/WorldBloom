# Supporting characters: accompaniment and rescue

The previous `*_shared_planning` editions give the protagonist a personal
shared goal. Their dogs, monkeys and other supporting characters still use
weighted legacy actions, including affinity-biased following and rescue.
Those choices are not shared causal plans. Their old target-item fields alone
do not mean they plan to help the protagonist.

The new `*_companions_planning` editions preserve those earlier editions and
add personal accompaniment purposes to the mobile declared companions in the
four Momotaro variants. The original protagonist goal and original cast remain.
The standard, plus and plus2 editions migrate three companions; plus3 migrates
nine, including the bodyguard. Grandparents, antagonists and the fisherman keep
their legacy decisions. This is not an all-character or all-story conversion.

## Common mechanisms

- A `co_location` concern compares two received position coordinates. It is
  not an assignable state flag. Unknown remote positions stay unknown.
- An optional objective `completion: period_end` postpones an achievement
  verdict until the end of the existing period. A temporary unmet success
  condition can be repaired; an explicit failure condition still ends the
  objective immediately. Omission preserves the old first-success behavior.
  `maintain` continues to fail on the first observed breach. This does not
  erase terminal history or imply that every intermediate moment was successful.
- Native rescue is a normal planning operator over another person's vitality.
  Execution uses the existing rescue handler, affinity threshold, verb and genre
  permissions. It never invents a capability or revives someone already dead.
- A witnessed one-edge departure supplies a last-seen destination and vitality
  observation. Multi-edge travel does not reveal the remote final destination
  to someone at the origin. Subsequent unseen changes are not broadcast.
- A nearby owner's permitted vehicle is visible to a borrower. Its presence
  remains an assumption rechecked by actual movement. Other possessions remain
  private. A remote owner does not supply local access to their boat.
- Same-place purposes order their search by rendezvous distance, avoiding
  unrelated transport-material collection before a simple local reunion.
- Waiting under a currently met purpose is rechecked before execution. A
  newly downed companion can invalidate an old wait and require a new plan.

All of these use the existing goal planner, concern evaluation and verb engine.
There is no engine dispatch on a character, story, place or item name. A
captain/medical-officer/engine-room test uses exactly the same movement and
rescue operators. Higher-priority personal goals and non-negotiable constraints
can override accompaniment; being a companion never overrides those choices.

## Authored personal purposes

The migration explicitly authors this purpose rather than deducing it from
personality: after the character's own affinity toward the protagonist reaches
the world's existing companionship threshold, they seek to be together and
keep the protagonist able to act. A downed protagonist can be rescued; observed
death is terminal. If the relationship drops below the threshold they stop
following. The threshold and priority are editable assumptions, not a new
universal model of agreement or loyalty. Below the threshold these migrated
characters wait, because no other personal purpose has yet been authored.

Conditions, success/failure rules and time periods live in the world. The
priority-8 purpose lives in each person's `commitments`. Traits remain the
existing parameters. The prior treasure `goal` fields are preserved for source
compatibility but no longer choose legacy actions for a migrated character.
Each migration manifest records source hashes, assumptions and people still
using legacy choices. Re-running generation refuses to overwrite edited files.

## Actual limitations

The existing ordinary movement handler moves one actor at a time. It does not
transport a group with a boat. If its owner departs first, a companion left on
the shore cannot use the remote boat. This edition does not teleport passengers,
grant extra boats, waive stamina or force a successful journey. Coordinated
boarding, safe waiting for a party, additional independent supporting purposes,
and the remaining legacy characters require further work.

## Reproduce

```
python tools/migrate_supporting_stories.py
python tools/run_supporting_story_comparison.py --out outputs/companions-comparison
python tools/run_supporting_story_comparison.py --out outputs/companions-accepted --sources momotaro --accepted
python -m unittest discover -s tests -p test_supporting_planning.py
```

The accepted-companion option is an explicitly different initial situation:
existing mobile companions begin with the protagonist and sufficient affinity.
It changes in-memory inputs, never the saved edition and never runtime choices.
Both variants preserve normal simulations, actor-level plan counts and actual
logs, without GA, external model calls or generated prose. These small trials
do not establish success probabilities or literary quality.

## Retained runs

`docs/evidence/supporting-story-migration.json` records 18 ordinary runs:
four sources, protagonist-only and companion editions, seeds 0/1 (16 runs),
plus the explicitly accepted-companion Momotaro initial situation for seeds 0/1.
Every actor in every run had zero invalid action results.

With unchanged initial affinity the new companions waited: none of these runs
established their acceptance. Thus enabling planning alone does not recruit
them. In the two accepted-initial runs the three companions actually followed
over land and ended at the shore. Neither journey reached its final target.
The absence of coordinated boat transport is a real unresolved limitation,
not a successful group journey. Rescue, changing priorities, affinity loss,
repeated reunion and captain/medic reuse are verified in direct execution tests;
a short normal simulation separately verifies an actual planned rescue.
