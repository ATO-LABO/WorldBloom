# Connecting character purposes to existing item transactions

`engine/objective_actions.py` adapts the existing `negotiate`, `concede` and
`give_item` verbs to character-owned `commitments` and world-owned `goal_rules`.
There are no additional personality genes, story IDs or saved configuration
blocks. Existing worlds without goal rules retain the old transaction behavior.
The original `goal.target` mechanism remains supported and is never overwritten.

## Authoring

1. Define an inventory condition for a character obtaining an item and give it
   a goal rule (achievement, irreversible failure, logical period).
2. Add that condition to the character's commitments, with priority and
   requiredness. Give the character the `negotiate` verb and enable its existing
   `objective_claimant` capability (default true).
3. Define a stock condition for the holder's item. The claimant must know that
   condition is true, through public visibility or a positive personal belief.
   Both people must be alive/revived and co-present. No remote inventory search
   or automatic route to a secretly stocked character is performed.
4. Give the holder `concede`. Its own priorities and required conditions govern
   accepting or refusing, in addition to the existing stance/attractive-item
   rule. Values may be entirely different from the claimant's.
5. Author any other causal means, such as raiding a store, with ordinary
   response operators. Their forecasts join the transaction comparison when
   a transaction is available. Phase, genre, permissions, evidence, resource
   and period checks still apply before comparison.

The inventory adapter obtains one unit per settlement. Larger requirements can
make genuine partial progress without declaring full achievement. Compound or
state goals are not automatically decomposed into item-acquisition subgoals;
authors can explicitly value an intermediate inventory condition as in the ship
example. A public condition is explicitly author-declared public information,
not permission to expose unrelated inventory or anyone's private commitments.

## Forecasts, priority and required conditions

Each transaction builds typed projections of the evaluating person's own
inventory and explicitly public inventory coordinates. Negotiation considers
the potential receipt of one target item and loss of the offered assets; the
forecast does not inspect the counterpart's private preferences or predict its
consent. The existing offer includes all positive, lootable items except the
target. This is a conservative payment ceiling: an eventual goodwill transfer
may cost less. Selecting subsets, counteroffers and bargaining are not added.

Affected required conditions must remain true and their explicit failure
expressions must remain false. A still-pending inventory acquisition may make
positive partial progress if its failure expression is false. Untouched pending
required goals do not prohibit every unrelated action.

Feasible means are compared by changes to the actor's own conditions, from
priority 10 down to 1, not by a sum that permits many minor gains to outweigh a
major loss. The first different priority decides. A newly satisfied condition
contributes +2, a newly unsatisfied condition -2; partial inventory progress
contributes the fraction of its required amount. Ties retain existing trait,
time, relationship and permission weights. Ordinary rest and other unrelated
legacy actions remain possible, so this does not make an entire simulation
deterministic. The comparison uses declared effects, not unconstrained language
planning or a learned forecast of future negotiations. It does not replace all
other legacy action policies.

## Real consent and execution

`negotiate` only creates an offer. It transfers no item and does not fulfil the
new goal. The holder later takes its own `concede` action; a new-purpose offer
can return `conceded` or `refused`. Refusal has no item or relationship-settlement
effects. The claimant sees only the answer, not a copy of the holder's private
values. Author-facing explanation records can show each person's own forecast.

A new-purpose offer is valid in the submitting logical point and the next one,
allowing either turn order. A claimant can wait for that answer, with an explicitly
conditional forecast. Silence then permits another means. Refused or unanswered
offers are not immediately repeated; retry is possible after four points from
submission. These are bounded transaction protocol timings, not new authored
personality parameters. Offers are keyed by claimant and holder, as before;
one pair has one current offer, not parallel contracts for multiple items.

Execution recomputes the own-value guard. It rechecks local people, the exact
item, original offered quantities, expiry, current purpose and claimant consent.
Missing payment cannot silently become a smaller payment or new property. The
holder can refuse if the claimant's originally submitted consent is no longer
valid. This validation is separate from the holder's candidate evaluation, which
never scores the claimant's private priorities. Transfers still use the existing
item removal/addition, trade/goodwill choice, relationship settlement and obstacle
resolution. Legacy offers keep their original rules and lifetime.

Existing gifts are checked against the giver's affected values. A new gift can
also be generated when it advances a valued, publicly visible recipient inventory
condition. Receiver-specific item restrictions remain in force. Gifts are not
negotiated exchanges and do not assume any reciprocal action.

## Examples and evidence

`projects/village_negotiation` starts with an ogre holding one craft item and a
storekeeper holding one food bag. The ogre values food at 10, village safety at
8, retaining the craft at 3. It proposes a trade. Raising only craft retention
to 9 makes raiding preferable; making village safety required rules raiding out.
Making both safety and craft retention required leaves no feasible means. A
storekeeper required to retain food refuses even if receiving the craft would
fulfil its higher-priority goal. The ogre must then reconsider its own means.

`projects/ship_negotiation` adds a causal dependency: a communicator obtains a
battery from the supply officer through the same old transaction verbs, then
consumes it in the authored beacon-sending action. Battery acquisition alone
does not send the beacon. Prioritizing tool retention can instead cause a forced
opening of the store, damaging its safety condition.

The explanation panel separates conditional personal forecasts, rejected means
and actual world outcomes. `tests/test_objectives_transactions.py` covers causal
switches, separate consent, expiry, stale resources, hidden knowledge, partial
progress, gift protection and the ship's two-stage outcome. Batch evidence must
retain all predeclared seeds, including failures; a software run completing is
not a claim that every person's story goal succeeded.
