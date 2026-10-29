# Protected conditions, version 1

## Contract fixed before implementation

A condition describes a desired state; a commitment says **who** values it,
its ordinal priority (higher first), and whether violating it is unacceptable.
Neither is a new gene. Existing temperament and the nine-gene Policy remain
separate. Conditions use registered runtime state, never an opaque concept name.

```json
{"version":1,"conditions":[
 {"id":"equal_eligibility","label":"Equal benefit eligibility","mode":"achieve","visibility":"public",
  "test":{"kind":"state","key":"eligibility","equals":"individual"}},
 {"id":"crew_alive","label":"Crew survival","mode":"maintain","visibility":"observed",
  "test":{"kind":"vitality","actor":"engineer","allowed":["alive","revived","downed"]}},
 {"id":"secret_kept","label":"No outsider knows the secret","mode":"maintain","visibility":"observed",
  "test":{"kind":"knowledge","fact":"secret","actors":["outsider"],"rule":"none"}}
],"commitments":{"proposer":[{"condition":"equal_eligibility","priority":2,"required":true}]}}
```

Expected initial results: unequal eligibility => raw `false`, progress `pending`;
living crew => `true`/`maintained`; outsider lacks secret => `true`/`maintained`.
If crew are dead => `false`/`breached`. A living child with a parent's explicit
false belief yields world `true` and actor `false`. No evidence yields actor
`unknown`, never automatic success or failure. An achieved condition is not
labelled breached merely because it has not yet been achieved.

The mode describes evaluation **at this observation point**. Continuous
never-breached invariants, completion deadlines and irreversible completion
memory are not asserted. Existing chronological logs retain earlier evaluations.
The existing proposal amendment deadline remains a separate procedural rule.

## State and perception

`state` reads existing deliberation state, `vitality` actual Subject vitality,
`knowledge` actual boolean fact ownership. The other adapters share this contract:
`inventory` minimum count, `zone` a person's location, and directed `relation`
minimum affinity. They do not equate every bond with one universal number;
authors must choose a concrete condition and explain its meaning in the label.

World evaluation can read truth. Actor evaluation can read explicitly public
conditions, their own state/inventory/knowledge/feelings, and visible co-present
vitality/location. Otherwise it uses their existing `Subject.beliefs[condition_id]`
(`value` is `true` or `false`, confidence in (0,1]) or returns unknown. Derived
condition IDs are validated separately from facts and must not collide with
registered facts, people, items or places. No second truth/knowledge store is
created. A belief is not checked against the true answer or silently corrected.
For a successful boolean `share_knowledge`, only its speaker and recipient get
an observation of that transfer. A knowledge condition can then update their
existing condition belief. Other people do not learn the transfer automatically.

## Proposal forecasts and decisions

An optional per-proposal `concern_projections` maps **every** alternative to
`{"requires":["fact-id"],"effects":[...]}`. Effects are hypothetical assignments
to one of the supported state adapters, not arbitrary code, not executed effects,
and not a claim that approval achieves the proposal. Each effect is shown with
the proposal. Actor predictions require firsthand prerequisite knowledge or
credited cited evidence from a current review the actor heard. Unjustified
forecasts are unknown. Unchanged properties use the actor's view, never hidden
world truth. The actual current world is evaluated separately after judgment.
Each reviewer's optional `evidence_trust` maps other registered reviewers to
credibility in [0,1], default 0.5. It is separate from the proposer's policy
trust. A reviewer never inherits the proposer's trust; the common per-proposal
evidence threshold merely controls whether testimony counts as a prerequisite.

Commitments are evaluated by priority tiers; at the highest tier that changes,
an improvement dominates lower tiers. Required false conditions oppose a plan;
required unknown conditions defer it. Soft values use the highest nonzero tier.
Unknowns do not fabricate benefit. Legacy term conditions can coexist, but the
examples keep their term preferences empty to avoid specifying the same value
twice. Conflict reasons identify the explicit conditions; no universal morality
or natural-language value discovery is inferred.

`choice_policy` version 2 keeps only version, trust, evidence_threshold,
alternative_requires, amendment_deadline and revision_steps. It requires
concern projections. It replaces goal_priority and compromise_limit with declared
commitments, leaving v1 unchanged. Candidate utility uses own priority-tier gain,
heard objections addressed, actual diligence/stubbornness, and revision effort.
Ordinary nine-gene reweighting still follows candidate generation where enabled;
the independent example runner does not claim to run GA.

V2 amendment utility is `8 * priority_gain * (0.5 + diligence) +
2 * credited_objection_relief * diligence - 8 * stubbornness -
0.25 * revision_steps`, relative to requesting judgment (weight 4).
`priority_gain` is the first differing priority tier's score difference divided
by twice the number of commitments in that tier, comparing **the current plan**
to the alternative, not comparing an imagined enacted plan to world truth.
Each condition contributes +1/0/-1 for true/unknown/false. This is an authored
ordinal model, not empirical psychology. It does not count repeated references
to the same commitment, inspect private peer values, or infer implementation
from authorization. Exponentiation/clamping is the same as choice policy v1.

The shared advanced-world editor supports all six condition types and separate
person commitments. Save and unsaved check use the existing revision-checked
world endpoint, validate the whole world with its actual people, and replace
only world.yaml atomically. Save reloads the workspace to prevent a stale file
editor from silently restoring old data. Explanations distinguish recorded actor
predictions from the author's actual-world report and escape authored labels.

Review and choice records retain actor assessments and public objections. Author
reports retain a separate world evaluation. Older worlds and old saved records
need neither block; absence enables no new behavior or fields.

## Work sequence and acceptance

1. Freeze this schema and the three expected-result examples above.
2. Implement world evaluation, then the remaining three adapter kinds.
3. Implement belief/unknown actor views; test hidden truth changes and mistaken
   beliefs without changing the actual state.
4. Connect reviews, amendments and reason records; test that authorization leaves
   eligibility, actual lives and secret ownership unchanged.
5. Exercise reform, rescue and disclosure worlds under matched seeds, preserve
   all outcomes, and expose human-readable conditions and priorities in the
   shared world editor and explanations. Validate references, preserve unrelated
   settings and old histories, and check desktop/narrow layouts.
