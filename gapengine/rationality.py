"""WB-JEV-001 Stage 2: the rationality table, the Ollama logprob judge, and
``Rationality`` -- the object a protagonist's ``Policy`` consults for a
relative plausibility multiplier ``m_rat`` on top of the four existing
genome-driven multipliers (m_cat/m_risk/m_stance/m_nov).

The Ollama call plumbing (``_ollama_call``/``_p_yes``/the choice-mode label
and chunking logic) moved here from ``scripts/rationality_probe.py`` in
Stage 2; the probe now imports these names instead of defining them, so its
own behavior (Mode A/B scoring, reports, ``--stats``/``--bench``) is
unchanged.

Determinism: ``Rationality.multipliers`` consumes no randomness. When
``kappa <= 0`` it renders nothing and never calls the judge -- see
``enabled`` -- so a disabled rationality layer costs nothing and leaves no
trace in a run's output (Policy/engine.sim only add the p_rat/m_rat meta
keys and the header's "rationality" key when enabled).
"""

from __future__ import annotations

import hashlib
import json
import math
import string
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

from gapengine.knowledge_text import (
    context_key,
    describe_candidate_coarse,
    map_line as knowledge_map_line,
    recipes_line as knowledge_recipes_line,
    render_situation,
    situation,
)
from gapengine.ollama import DEFAULT_BASE_URL, DEFAULT_MODEL, build_request

QUESTION = (
    "質問: 本人の知る限りで、この行動は目的に近づく手段として筋が通っているか。"
    "yes か no の1語だけで答えよ。"
)
CHOICE_QUESTION = (
    "質問: 本人の知る限りで、目的に近づく手段として最も筋が通っているのはどれか。"
    "記号1文字だけで答えよ。"
)
LABELS = tuple(string.ascii_uppercase + string.ascii_lowercase)  # A..Z, a..z


# ---------------------------------------------------------------------------
# Ollama call + logprob readout (moved from scripts/rationality_probe.py)
# ---------------------------------------------------------------------------


def _ollama_call(
    model: str,
    prompt: str,
    *,
    base_url: str,
    timeout: float,
    top_logprobs: int = 10,
) -> dict[str, Any]:
    url, payload = build_request(
        {
            "base_url": base_url,
            "model": model,
            "think": False,
            "options": {"num_predict": 1, "temperature": 0, "seed": 0},
        },
        prompt,
    )
    payload["logprobs"] = True
    payload["top_logprobs"] = top_logprobs
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _top_logprobs(data: dict[str, Any]) -> list[Any]:
    logprobs = data.get("logprobs")
    if not isinstance(logprobs, list) or not logprobs:
        return []
    top = logprobs[0].get("top_logprobs")
    return top if isinstance(top, list) else []


def _p_yes(data: dict[str, Any]) -> tuple[float | None, list[Any]]:
    """(p_yes, raw top_logprobs). None when neither a yes- nor no-token is
    among the top logprobs at all (欠測)."""

    top = _top_logprobs(data)
    yes_mass = no_mass = 0.0
    found_yes = found_no = False
    for entry in top:
        token = entry.get("token")
        logprob = entry.get("logprob")
        if not isinstance(token, str) or not isinstance(logprob, (int, float)):
            continue
        normalized = token.strip().lower()
        if normalized == "yes":
            yes_mass += math.exp(logprob)
            found_yes = True
        elif normalized == "no":
            no_mass += math.exp(logprob)
            found_no = True

    if not found_yes and not found_no:
        return None, top
    total = yes_mass + no_mass
    return (yes_mass / total if total > 0 else 0.0), top


def _label_masses(data: dict[str, Any], labels: Sequence[str]) -> dict[str, float]:
    """exp(logprob) mass per label token actually seen in top_logprobs. A
    label absent from top_logprobs gets 0.0."""

    mass = {label: 0.0 for label in labels}
    label_set = set(labels)
    for entry in _top_logprobs(data):
        token = entry.get("token")
        logprob = entry.get("logprob")
        if not isinstance(token, str) or not isinstance(logprob, (int, float)):
            continue
        normalized = token.strip()
        if normalized in label_set:
            mass[normalized] += math.exp(logprob)
    return mass


def _normalize(mass: dict[str, float]) -> dict[str, float]:
    total = sum(mass.values())
    if total <= 0.0:
        return {label: 0.0 for label in mass}
    return {label: value / total for label, value in mass.items()}


def _measure_top_logprobs_limit(model: str, *, base_url: str, timeout: float) -> int:
    """One throwaway call with top_logprobs=50 to see how many entries the
    server actually returns. Used to size choice-mode candidate chunks."""

    probe_prompt = "質問: 「A」か「B」か。記号1文字だけで答えよ。\n\n答え:"
    data = _ollama_call(model, probe_prompt, base_url=base_url, timeout=timeout, top_logprobs=50)
    return len(_top_logprobs(data))


# ---------------------------------------------------------------------------
# RationalityTable: the persisted context->p_rat map
# ---------------------------------------------------------------------------


class RationalityTable:
    """``dict[str, float]``: context/candidate key -> judge probability.
    Persisted as one sorted, UTF-8 JSON file per experiment (out_dir /
    "rationality.json") shared by every generation and every candidate
    (WB-JEV-001 Stage 2 plan §1.1)."""

    def __init__(self, mapping: Mapping[str, float] | None = None) -> None:
        self._data: dict[str, float] = dict(mapping or {})

    def get(self, key: str) -> float | None:
        return self._data.get(key)

    def update(self, mapping: Mapping[str, float]) -> None:
        self._data.update(mapping)

    def to_dict(self) -> dict[str, float]:
        return dict(self._data)

    def __len__(self) -> int:
        return len(self._data)

    @property
    def hash(self) -> str:
        serialized = json.dumps(self._data, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def load(cls, path: str | Path) -> "RationalityTable":
        location = Path(path)
        if not location.is_file():
            return cls()
        raw = json.loads(location.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Rationality table must be a JSON object: {location}")
        return cls({str(key): float(value) for key, value in raw.items()})

    def save(self, path: str | Path) -> None:
        location = Path(path)
        location.parent.mkdir(parents=True, exist_ok=True)
        location.write_text(
            json.dumps(self._data, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Judges
# ---------------------------------------------------------------------------


class NullJudge:
    """Always-None judge (backend "none"). Used when no LLM backend is
    configured -- every candidate falls back to a multiplier of 1.0."""

    backend_name = "none"
    model: str | None = None

    def score(self, context_text: str, candidates: Sequence[str]) -> list[float | None]:
        return [None] * len(candidates)


class OllamaLogprobJudge:
    """Scores candidates against a local Ollama model's yes/no ("noul") or
    multiple-choice ("choice") logprob readout. Never raises: a request
    failure (HTTP error, timeout, or a response missing every expected
    token) yields ``None`` for the affected candidate(s), never an
    exception (WB-JEV-001 Stage 2 plan §1.2)."""

    backend_name = "ollama"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 300.0,
        method: str = "noul",
    ) -> None:
        if method not in ("noul", "choice"):
            raise ValueError(f"Unknown rationality judge method: {method}")
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.method = method
        self._chunk_size: int | None = None

    def score(self, context_text: str, candidates: Sequence[str]) -> list[float | None]:
        candidates = list(candidates)
        if not candidates:
            return []
        if self.method == "choice":
            return self._score_choice(context_text, candidates)
        return self._score_noul(context_text, candidates)

    def _score_noul(
        self, context_text: str, candidates: list[str]
    ) -> list[float | None]:
        results: list[float | None] = []
        for desc in candidates:
            prompt = f"{context_text}\n\n候補: {desc}\n\n{QUESTION}"
            try:
                data = _ollama_call(
                    self.model, prompt, base_url=self.base_url, timeout=self.timeout
                )
            except (OSError, urllib.error.URLError, ValueError):
                results.append(None)
                continue
            p_yes, _top = _p_yes(data)
            results.append(p_yes)
        return results

    def _score_choice(
        self, context_text: str, candidates: list[str]
    ) -> list[float | None]:
        if len(candidates) == 1:
            return [1.0]
        if self._chunk_size is None:
            try:
                limit = _measure_top_logprobs_limit(
                    self.model, base_url=self.base_url, timeout=self.timeout
                )
            except (OSError, urllib.error.URLError, ValueError):
                limit = 10
            # Capped at len(LABELS): a chunk larger than the label alphabet
            # would silently drop the tail candidates below.
            self._chunk_size = min(limit if limit >= 2 else 10, len(LABELS))

        results: list[float | None] = [None] * len(candidates)
        for start in range(0, len(candidates), self._chunk_size):
            chunk = candidates[start : start + self._chunk_size]
            labels = LABELS[: len(chunk)]
            lines = [context_text, "", "候補:"]
            lines.extend(f"{label}: {desc}" for label, desc in zip(labels, chunk))
            lines.extend(["", CHOICE_QUESTION])
            prompt = "\n".join(lines)
            try:
                data = _ollama_call(
                    self.model,
                    prompt,
                    base_url=self.base_url,
                    timeout=self.timeout,
                    top_logprobs=max(len(labels), 10),
                )
            except (OSError, urllib.error.URLError, ValueError):
                continue
            probs = _normalize(_label_masses(data, labels))
            for offset, label in enumerate(labels):
                results[start + offset] = probs[label]
        return results


# ---------------------------------------------------------------------------
# Rationality: the Policy-side coordinating object
# ---------------------------------------------------------------------------


class Rationality:
    """Renders a decision point's situation, looks up (or fills) a
    relative-plausibility table, and turns it into a per-candidate
    multiplier ``m = (p / mean(p over this point's scored candidates)) **
    kappa``. Consumes no randomness.

    One instance is meant to live for exactly one simulation run (one
    seed): ``judge_calls``/``new_entries`` describe what *this* instance
    contributed, so a caller can persist them per run and merge across runs
    (WB-JEV-001 Stage 2 plan §1.3/§1.6)."""

    def __init__(
        self,
        *,
        kappa: float,
        table: RationalityTable,
        judge: Any,
        common_knowledge: Sequence[str] = (),
        method: str = "noul",
        key_items: Sequence[str] = (),
        max_judge_calls: int | None = None,
    ) -> None:
        self.kappa = float(kappa)
        self.table = table
        self.judge = judge
        self.common_knowledge = list(common_knowledge)
        self.method = method
        self.key_items = list(key_items)
        self.max_judge_calls = max_judge_calls
        self._judge_calls = 0
        self._budget_exhausted = False
        self._new_entries: dict[str, float] = {}
        self._table_hash_at_start = table.hash
        self._recipe_lines: str | None = None
        self._map_line: str | None = None

    @property
    def enabled(self) -> bool:
        return self.kappa > 0.0

    @property
    def new_entries(self) -> dict[str, float]:
        return dict(self._new_entries)

    @property
    def meta(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "kappa": self.kappa,
            "method": self.method,
            "backend": getattr(self.judge, "backend_name", None),
            "model": getattr(self.judge, "model", None),
            "table_hash_at_start": self._table_hash_at_start,
            "judge_calls": self._judge_calls,
        }
        if self._budget_exhausted:
            value["budget_exhausted"] = True
        return value

    def multipliers(
        self,
        subject: Any,
        world: Any,
        present: Sequence[Any],
        actions: Sequence[Any],
    ) -> tuple[list[float], list[float | None]]:
        """(m_rat per action, p_rat per action). rng-free. Returns
        ``([1.0]*n, [None]*n)`` without rendering or calling the judge when
        disabled (``kappa <= 0``) -- the cost-free/byte-identical path."""

        n = len(actions)
        if not self.enabled or n == 0:
            return [1.0] * n, [None] * n

        if self._recipe_lines is None:
            self._recipe_lines = knowledge_recipes_line(subject, world)
            self._map_line = knowledge_map_line(world)

        descs = [
            describe_candidate_coarse(action, subject, world, present)
            for action in actions
        ]
        sit = situation(subject, world, present, key_items=self.key_items)
        context_text = render_situation(
            sit,
            world,
            common_knowledge=self.common_knowledge,
            recipe_lines=self._recipe_lines,
            map_line=self._map_line,
        )

        unique_descs = list(dict.fromkeys(descs))
        if self.method == "choice":
            set_key = context_key(
                context_text + "\n候補集合: " + "|".join(sorted(unique_descs))
            )
            keys = {desc: f"{set_key}:{desc}" for desc in unique_descs}
        else:
            keys = {
                desc: context_key(f"{context_text}\n候補: {desc}")
                for desc in unique_descs
            }

        missing = [desc for desc in unique_descs if self.table.get(keys[desc]) is None]
        if missing:
            if (
                self.max_judge_calls is not None
                and self._judge_calls >= self.max_judge_calls
            ):
                self._budget_exhausted = True
            else:
                scored_descs = unique_descs if self.method == "choice" else missing
                scored = self.judge.score(context_text, scored_descs)
                self._judge_calls += 1
                new_entries = {
                    keys[desc]: p
                    for desc, p in zip(scored_descs, scored)
                    if p is not None
                }
                self.table.update(new_entries)
                self._new_entries.update(new_entries)

        p_by_desc = {desc: self.table.get(keys[desc]) for desc in unique_descs}
        p_list = [p_by_desc[desc] for desc in descs]
        scored_values = [p for p in p_list if p is not None]
        mean = sum(scored_values) / len(scored_values) if scored_values else None
        m_list = [
            (p / mean) ** self.kappa
            if (p is not None and mean is not None and mean > 0.0)
            else 1.0
            for p in p_list
        ]
        return m_list, p_list
