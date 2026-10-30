# Proposal deliberation v1

This opt-in feature extends the existing `World`, `Subject`, action candidates,
`VerbEngine`, seeded `Simulation`, safe ending predicates and saved run records.
It is not a second simulator, a language-model judge, or a political predictor.
The implementation is based on develop `ebfb780`, which merged PR #47 head
`8a23e10`. The merge was fetched during this work; its tree is identical to the
PR head inspected at the start. This change adds only deliberation functionality.

## What the first episode establishes

In `projects/reform_hearing`, a civil servant proposes ending marital status as
a condition of state benefits. The first judgment authorizes formal examination,
rejects the proposal, or defers for missing information. It cannot enact a law:
the configured `marriage system` state remains in force for every outcome.
This is a fictional country with authored rules, not current Japanese law.

`projects/rescue_council` uses exactly the same implementation with different
actors, terms, constraints, knowledge and state effects. A navigation officer
seeks permission to divert a returning spacecraft for rescue. Authorization
does not rescue anyone; the survivor state is deliberately unchanged.

## Input and semantics

An optional `world.yaml` `deliberation` block has version 1, explicit initial
state, and a bounded proposal list. Each proposal defines:

- proposer, authority, reviewers, required supporters, quorum and meeting place;
- explicit starting-state requirements, checked when first submitting;
- finite alternatives, each specifying every term, a label and authored speech;
- one profile per proposer/reviewer: integer term scores, a minimum score,
  nonnegotiable allowed values, prerequisite knowledge, and speech;
- explicit approved/rejected/deferred state effects and decision statements.

There is no literal marriage, ministry, spacecraft, school, actor name or term
name in the runtime. Terms are bounded strings, not executable predicates.
Profiles and numerical values are author settings, not inferred psychology.
They are separate from Subject relationship affinity and never modify it.
Known facts come from the real Subject.knowledge and registered World facts.
An existing boolean fact here establishes knowledge of authored evidence; it
does not establish a real-world budget calculation or physical fuel prediction.

The common action sequence is:

1. `submit_proposal(proposal, initial_alternative, "0")` presents the first version.
2. `present_proposal(proposal, reviewer, revision)` briefs a late participant.
3. `review_proposal(proposal, revision)` declares the actor's position and reasons.
4. After hearing all current reviews the proposer can either
   `amend_proposal(proposal, alternative, revision)` or
   `request_decision(proposal, revision)`.
5. `decide_proposal(proposal, revision)` is available only to the authority after
   the proposer requests judgment and every reviewer has a fresh current review.

Participants must be alive/revived, have the verb and be in the meeting place.
Ordinary genre and phase-verb restrictions still apply. Reviews are spoken in
the authority's presence. Attending participants learn presented versions;
absent people do not. An amendment may respond only to objections the proposer
heard, not to a private reviewer profile. Without `choice_policy`, proposer
candidates retain the original fixed weights and own authored score. With the
optional policy below they use situated, logged motivations. No step plans an arbitrary
natural-language objective or reads another person's private motivation to
invent a successful amendment.

Unknown required knowledge produces `undecided`, independently of term scores.
Otherwise hard constraints and the explicit score threshold determine assent.
Each review records its actor, audience, version, terms hash, score, blockers,
known/missing evidence and literal statement. New knowledge requires a fresh
review. A reviewer who is no longer alive cannot supply a current review.

Amendment creates a new monotonically numbered, content-hashed version and
clears current assent. Old reviews remain in history. Used alternatives cannot
be recycled, so revisions are finite. Requesting judgment locks amendments;
authority cannot preempt the proposer. A decision is terminal for this proposal.

If any current reviewer is undecided, judgment is deferred. Otherwise approval
requires authority support, every declared required supporter, and quorum.
Failure is rejection. Only the configured state effects are applied. Old
assent, friendship or a narrative description cannot manufacture approval.

Endings can read `proposal_decided("id", "approved")` (also rejected/deferred)
or `proposal_state("key", "value")`. These are evaluated by the real engine.
All other worlds keep their former candidates and log fields.

## Run and inspect

From the repository root, with Python 3.12+ and PyYAML:

```text
python -B scripts/deliberation_sim.py --world projects/reform_hearing/world.yaml --out <new-output-folder> --trials 24
python -B scripts/deliberation_sim.py --world projects/rescue_council/world.yaml --out <another-new-folder> --trials 24
python -B -m unittest discover -s tests -p test_deliberation.py -v
```

The helper runner accepts self-contained worlds only, snapshots the world and
all people before the first trial, declares seeds 0..N-1, and uses the existing
verified independent-trial ExecutionPlan. It retains all slots including
execution failures and unresolved outcomes. It never selects, breeds, changes
profiles or feeds a chosen story back into the batch. Output folders must be
new. This is not a success-rate estimate for arbitrary story premises.

Each run has the normal `layers.jsonl` and bound `run-record.json`. Deliberation
transitions are effective deltas, including complete before/after state. The
batch includes exact input hashes, source hashes, plan, all trial rows and summary.
`scene-material.json` is extracted only after checking the saved descriptor and
ledger digest; it includes literal speech and source line references. It is
author evidence, not generated prose. Altering the ledger invalidates it.

The delivered prose selects existing trial reform/4 (protected amendment),
reform/2 (original rejected), and rescue/4 (reserve-fuel amendment). All seeds
remain available. Authors may add gestures and narration as editorial additions;
actors, order, versions, judgments and the limited state effects remain bound to
the chosen log. There is no LLM or GPU call in these runs.

## Situated proposer choice (optional, version 1)

The two example worlds now enable `choice_policy` per proposal. Old worlds that
omit it keep exactly their former candidates and weights. The new policy reads
the real proposer's `Subject.traits.stubbornness` and `diligence` (finite 0..1),
their own profile and `Subject.knowledge`, and the current reviews whose audience
included them. It does not infer these traits from a personality paragraph.

All policy fields are explicit:

```json
{
  "version": 1,
  "goal_priority": 2.0,
  "compromise_limit": 4,
  "trust": {"reviewer-id": 0.9},
  "evidence_threshold": 0.6,
  "alternative_requires": {"amended-id": ["registered-fact-id"]},
  "amendment_deadline": 7,
  "revision_steps": 2
}
```

`goal_priority` is authored pressure to obtain assent (0..10), not free-text goal
planning. `compromise_limit` bounds the loss of the proposer's own term score.
Hard personal constraints still exclude an alternative regardless of utility.
`trust` is the proposer's explicit credibility estimate for each speaker (0..1;
unspecified speakers default to 0.5). It is independent of affection; it never
changes the actual reviewer's assent. `amendment_deadline` can be null. Otherwise
an amendment is excluded if the current logical turn plus authored `revision_steps`
exceeds it. This is an estimated revision/review allowance, not real elapsed
minutes, a guarantee of completion, or a reservation of others' turns.

Prerequisites in `alternative_requires` are met either by the proposer's own
knowledge or by evidence explicitly cited in a current review they heard. The
structured review's `known_evidence` and `blockers` are communicated with that
action. A cited fact independently known by the proposer gives credibility 1;
otherwise the speaker's trust must meet `evidence_threshold`. Reported evidence
stays labelled `heard_testimony`: it is never inserted into `Subject.knowledge`,
never inferred from an unrelated fact, and never obtained from an absent or
unheard reviewer. This is an authored boolean-evidence model, not truth checking
or general learning from natural-language speech.

After everyone has reviewed, requesting judgment has baseline weight 4. Each
unused amendment must pass capability, own assent, score-loss budget, evidence,
and time checks. For each **heard opposing** review, it can anticipate relief
only if it addresses every explicitly stated blocker. A mandatory supporter or
the authority has importance 2; another reviewer has importance 1. Relief is the
sum of credibility times importance divided by total reviewer importance.
Unknown evidence does not become predicted assent. Newly changed terms can
produce new objections; actual re-review is still required.

The relative utility is fully declared, not learned psychology:

```text
benefit = 4 * goal_priority * relief * (0.5 + diligence)
resistance = 8 * stubbornness
compromise = own_score_loss * (0.5 + stubbornness)
effort = 0.25 * revision_steps
utility = benefit - resistance - compromise - effort
amendment_weight = 4 * exp(clamp(utility, -12, 12))
```

The ordinary candidate filter, any existing action policy, and the actual seeded
Simulation sampler still run. Rest and other available verbs remain possible.
Different motivations change the distribution; they do not mandate one outcome.
Low trust is an authored estimate, not proof that a speaker is wrong. Neither
trust nor popularity overrides procedural authority or required assent.

Every applied amendment/request records `deliberation_event.choice_basis` with
the prior revision/hash, logical turn, traits, policy, firsthand knowledge,
heard reviews/credibility, eligible and excluded alternatives, evidence sources,
score loss and all utility factors, plus the chosen action. Its weights are
explicitly **before** other candidates and policies; the normal ledger's
`choice_prob` is the actual sampling probability. Extraction into scene material
preserves these reasons and verifies the saved episode. It does not invent
interiority or claim that a stochastic draw had one exclusive psychological cause.

For a controlled comparison, retain the same original world, reviews and RNG
state and vary one declared input. Tests cover personality, knowledge, trust,
goal priority, deadline, privacy, hard limits, stale actions and both genres:

```text
python -B -m unittest discover -s tests -p "test_deliberation*.py" -v
```

The earlier delivered prose and its 48 trials remain evidence for the original
fixed-weight implementation. They are not retroactively relabelled as motivated
choices. New runs must retain their new exact input and source snapshots.

## Bounds and unfinished integration

The first version uses finite authored proposals, explicit preferences, one
meeting place per proposal and the legacy logical-slot clock. Labels are not
elapsed real minutes. It does not model elections, all legal procedures,
coalitions, unspoken bargaining, continuous numerical resources, enforcement,
or policy consequences after the first decision. It does not infer truth or
belief changes from persuasive text. Unknown situations must remain unknown.

The minute-calendar runtime explicitly rejects this block until scheduling,
checkpoint and promise interaction contracts are designed. Existing PromiseRegistry
records task agreements, not assent to proposal text; its assent is not reused.
Multiple proposal start conditions are checked only at submission; there is no
automatic transaction/planning layer across proposals.

PR #47's AI draft schema and interactive editor have not been extended to author
deliberation blocks. These examples run through the provided command and the
ordinary engine. A shared proposal editor and broader goal-driven planning are
future units, not claimed by the tests or prose in this change.
