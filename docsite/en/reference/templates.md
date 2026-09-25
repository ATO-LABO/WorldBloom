---
ja_rev: "106d1344ed2a"
---
# Templates

A "genre" (`templates/<genre>/`) defines a world's grammar, and a "world" (`projects/<world>/`) defines a concrete setting that uses that grammar. A genre can be shared across multiple worlds (e.g. `templates/momotaro_plus2` is referenced from `projects/momotaro_plus2`).

## Layout of `templates/<genre>/`

| File | Role |
|---|---|
| `action_graph.yaml` | The list of available actions (verbs), plus each one's action category `category` (I–VI), `subtype`, `risk`, and `sign` |
| `canon.yaml` | Pseudo-observation counts of context → action for "what's commonly seen in this genre" (the canon prior). What a novelty-seeking genome avoids, and the initial value of generation 0's precedent table |
| `effects.yaml` | The foreshadowing (delayed effect) library: setup conditions (`plant`) and payoff conditions/effects (`payoff`) |
| `qd.yaml` | The QD grid's axes: `categories` (row = leading category) and `volatility_bins` (column = volatility band) |
| `rules.yaml` | Modifier rules. While a predicate holds, they temporarily adjust the genome's weights |
| `rationality.yaml` (optional) | κ's (Jev rationality judgment) default value and the judge's backend settings. A genre without this behaves as if κ=0 |
| `route.yaml` (optional) | Settings for the [route layer](../concepts/route-layer.md) (ρ's default, per-classification multipliers, etc.). A genre without this has no route layer at all (run settings shows no 05. Route section either) |
| `motives.yaml` (optional, paired with route.yaml) | The route layer's motive table: a list of rules that gives a "no reason" detour a reason |

All are YAML, and predicate strings (`when`, `condition`) are read with `engine/predicate.py`'s whitelist evaluator (`ast`-based; `eval` is never used). The same evaluator is shared across four places: modifier rules, foreshadowing payoff conditions, ending conditions (`world.yaml`'s `ending.when`), and `phase_rules`.

### action_graph.yaml

`templates/momotaro_plus2/action_graph.yaml` has four sections: `nodes:` (the action list), `edges:` (prerequisites between actions), `permission:` (allowed level per target role), and `restricted_weight:` (the weight multiplier for `restricted`).

```yaml
nodes:
  - {verb: train, category: I, subtype: self_strengthen, risk: neutral, sign: 0}
  - {verb: fight, category: I, subtype: weaken_direct, risk: risky, sign: -1}

permission:
  fight: {hostile: allow, neutral: restricted, ally: restricted}

restricted_weight: 0.15
```

`category` is a row of the QD grid (I self-reinforcement, II perception & foreshadowing, III relationship building, IV Identity, V movement & stalling, VI external intervention). `sign` is the action's directionality (+1 cooperative, -1 hostile, 0 neutral), used in computing novelty and quality q.

Classification is context-dependent: the same verb in `nodes:` can land on a different row depending on whether its `when` condition holds. For example `investigate` is category I (self-reinforcement) when `when: gather` holds, otherwise II (perception & foreshadowing). `move` is category V (movement & stalling) when `crossing` (an identity-passage move) holds, otherwise `category: null` (outside the QD grid).

`permission:` gives an `allow`/`restricted`/`deny` level per target role (`hostile`/`neutral`/`ally`). `restricted` doesn't exclude the candidate — it stays in the candidate pool with its weight scaled down by `restricted_weight` (default 0.15).

### canon.yaml

```yaml
weight: 1.0
entries:
  - ctx:
      phase: []
      hostile_present: false
      objective: hostile
      vitality: alive
      stance: hostile
    act: {category: III, verb: give_item, role: neutral}
    n: 6
```

`n` is the pseudo-observation count of taking action `act` in context `ctx`. It's mixed into the precedent table via Laplace smoothing as `p(act|ctx) = (n(ctx,act)+1) / (n(ctx)+|A_ctx|)` (`weight` is canon's overall weight `w_canon`).

### effects.yaml

```yaml
- id: kibidango_loyalty
  plant:
    verb: give_item
    item: きびだんご
    to_role: [neutral, ally]
  payoff:
    condition: "stance(target, planter) >= 0.5 and hostile_present(planter)"
    description: きびだんごの恩に報いる仲間の奮戦
    effect:
      modifier: {target: planter, source: "$target", value: 10, kind: loyal}
    mode: auto
```

`mode: auto` fires automatically once the conditions are met (a natural consequence of the action); `mode: chosen` instead offers a "pay it off" action as a candidate, chosen according to the genome (the timing itself becomes part of the search). If the story ends without paying off a `chosen` one, quality q is docked -0.05 per unresolved case.

### qd.yaml

```yaml
categories: [I, II, III, IV, V, VI]
volatility_bins: [low, mid, high]
```

Among the bundled genres (under `templates/`: `basic`, `detective`, `momotaro`, `momotaro_plus`, `momotaro_plus2`, `romance`), `momotaro` (the Momotaro family) uses all six action categories I–VI, while `detective` and `romance` use only the three categories I–III as axes (their `action_graph.yaml` itself covers a narrower range of actions). `volatility_bins` is `[low, mid, high]` for every genre. The thresholds are computed automatically from generation 0's population variance and recorded in `archive.json`'s `volatility_thresholds`.

### rules.yaml

```yaml
- id: hostile_lean
  scope: candidate
  when: "stance(self, target) < -0.3"
  adjust:
    category_weight.I: 0.2
    risk_tolerance: 0.1
  description: 敵対相手にはより攻撃的に
```

`scope` is either `candidate` (per candidate — `target` gets bound) or `turn` (per turn). `adjust` keys are `category_weight.<I–VI>`, `risk_tolerance`, `stance_shift_bias`, or `novelty_drive`, added temporarily. It's used as the actual weight via `g_eff = clip(genome + Σ matching rules' adjust)`.

### rationality.yaml (κ / Jev)

```yaml
kappa: 0.0
method: choice
backend:
  type: ollama
  model: qwen3.6:35b
  base_url: http://localhost:11434
  timeout: 300
key_items: [きびだんご, 小判]
```

κ is the strength of that genre's rationality judgment (Jev); run settings can override it with a value from 0–1 (see [Run Settings](../usage/run-settings.md)). A genre without this file behaves as if κ=0 (no judgment used).

### route.yaml (ρ / route layer)

```yaml
holder_belief_fact: treasure_thief
trial_reveal_facts:
  鬼の弟: 弟の消息

rho: 0.0
multipliers:
  advance: 1.0
  prepare: 0.5
  detour:body: 1.0
  detour:belief: 1.0
  detour:ignorance: 0.5
  detour:none: 0.02
  lost: 1.0

min_win_prob: 0.2

gene_affinity: 0.5
route_category:
  fight: I
  negotiate: III
```

| Key | Meaning |
|---|---|
| `holder_belief_fact` | The ID of a fact the protagonist could be mistaken about regarding the goal item's holder (used by the [route layer](../concepts/route-layer.md)'s "belief" classification) |
| `trial_reveal_facts` | A mapping (giver ID → fact ID) saying "this trial's giver doesn't appear as a waypoint in the planner until the subject has learned the paired fact" |
| `rho` | ρ's default value. Can be overridden by 05. Route in run settings. Default 0.0 (unmodulated) |
| `multipliers` | The base multiplier b per classification (`advance`/`prepare`/`detour:body`/`detour:belief`/`detour:ignorance`/`detour:none`/`lost`). Omitted keys keep their default |
| `min_win_prob` | The minimum believed win probability for fighting/sabotaging/neutralizing the holder to count as "a fight worth taking." Below it, the action is a "no reason" detour (unless the motive table reclassifies it) |
| `gene_affinity` | When more than one way exists to take the goal item from its holder (e.g. fight vs. negotiate), how much the genome tilts which one counts as the best plan (0–1, default 0 = always the lowest-cost one) |
| `route_category` | The mapping `gene_affinity` reads from, between a route name (`fight`/`negotiate`, etc.) and an action category (I–VI) |

A genre without this file has no route layer at all (run settings shows no 05. Route section either).

### motives.yaml (motive table)

```yaml
- id: care_for_ally
  label: 仲間を大事にする
  when: "stance(self, target) >= 0.6"
  verbs: [give_item, share_knowledge, persuade, pledge]
  gene: category_weight.III
  text: "{target}との絆を深めたくて{verb_text}"
```

An optional file that can only exist for genres with a `route.yaml`. The first entry (reading top to bottom) whose condition (`when`) and target action (`verbs`) both match wins.

| Key | Meaning |
|---|---|
| `id` | The motive's unique ID |
| `label` | The short name shown in the on-screen badge |
| `when` | A predicate string (the `engine/predicate.py` evaluator). `target` binds to the candidate's target (or self, for a candidate with none) |
| `verbs` | The verbs this motive can apply to |
| `gene` | The genome key read for the motive's strength (`risk_tolerance`, `stance_shift_bias`, `novelty_drive`, or `category_weight.<I–VI>`). A leading `-` inverts it (e.g. `-risk_tolerance` is stronger the lower risk tolerance is) |
| `text` | A reason-text template. Can interpolate `{target}`, `{verb_text}`, etc. |

The motive table only ever applies to candidates that would otherwise be a "no reason" detour — it never touches advance/prepare/body/belief/ignorance/lost. A matched motive's actual weight scales continuously with the strength (0–1) of the gene named in `gene` (see [Route Layer](../concepts/route-layer.md)).

## Layout of `projects/<world>/`

| Path | Role |
|---|---|
| `world.yaml` | The stage itself (place names, routes, world-wide settings and ending conditions, excluding the characters' initial relationships) |
| `subjects/*.yaml` | One file per character (their seven layers' initial state) |

### Main keys in world.yaml

```yaml
name: 桃太郎＋2
time: {days: 20, slots: [朝, 昼, 夕方, 夜]}
protagonist: 桃太郎
antagonist: 鬼
zones: [{name: 村, note: ...}, ...]
routes:
  村: [{to: 道中}]
  海: [{to: 鬼ヶ島, cost: 4, requires_item: 船}]
gapengine:
  action_graph: templates/momotaro_plus2/action_graph.yaml
  effects: templates/momotaro_plus2/effects.yaml
items:
  - {name: 船, made_from: {木材: 2, 縄: 1}, requires: {knowledge: 造船術}, vehicle: true}
facts:
  - {id: oni_weakness, values: [金棒, 火, 塩], secrecy: 0.4, act_threshold: 0.6}
truth: {oni_weakness: 金棒, treasure_thief: 鬼}
ending:
  - id: homecoming
    when: {agent: 桃太郎, goal: attained}
    label: 鬼退治を果たし、宝を村へ持ち帰った
target_ending: [homecoming, homecoming_shared]
```

`ending.when` takes either a predicate string or `{agent, goal}`. Only runs that reach an ending listed in `target_ending` enter the [QD Map](../concepts/qd-map.md)'s archive. `zones`/`routes` are the world-side data behind the seven layers' Phase layer (the basis for `phase_rules`' crossing checks), `items` behind the Resource layer (they end up in `inventory`), and `facts` behind the Perception layer (the source of `knowledge`/`beliefs_about`) (see [Seven Layers](../concepts/seven-layers.md)).

### Main keys in subjects/*.yaml

```yaml
id: おじいさん
traits: {social: 0.55, stubbornness: 0.45, curiosity: 0.35, diligence: 0.75, temper: 0.30}
base: 10
modifiers: []
knowledge: []
inventory: {}
phase: []
verbs: [share_knowledge, give_item, rest]
identity: {"true": 山へ芝刈りに行くおじいさん, displayed: おじいさん}
goal: {target: null, deliver_to: null, obstacles: [], outcome: null}
relations:
  桃太郎: {affinity: 0.85, awareness: 1.0}
stamina: {max: 6, recover_per_slot: 0.5}
range: {zones: [村], entry: 村}
```

Everything except `traits` (fixed disposition, outside the GA's reach) is a seven layers initial value. `relations` is the initial value of the relation matrix `world.relations[a][b] = {affinity, awareness}`, readable from the predicate evaluator as `stance(a,b) = affinity(a→b)`.
