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
import subprocess
import time
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
    # Ollama rejects an over-limit top_logprobs with HTTP 400 (observed: 50 ->
    # 400 on 0.34.1; 20 is the OpenAI-convention maximum), so step down.
    for requested in (20, 10, 5):
        try:
            data = _ollama_call(
                model, probe_prompt, base_url=base_url, timeout=timeout,
                top_logprobs=requested,
            )
        except urllib.error.HTTPError:
            continue
        return len(_top_logprobs(data))
    return 10


def _read_gpu_temperature() -> float | None:
    """Best-effort GPU temperature (Celsius) via ``nvidia-smi``. Returns
    None -- silently, never raising -- when nvidia-smi isn't installed,
    times out, or the machine has no NVIDIA GPU; the thermal guard treats
    None as "can't tell, don't block" (2026-09-19 thermal-guard addendum:
    this machine hit 86C and a GPU-lost event under sustained Ollama load).
    A module-level function, not a method, specifically so a test can
    monkeypatch ``gapengine.rationality._read_gpu_temperature`` without
    needing a real GPU."""

    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return float(completed.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def _even_chunks(items: Sequence[str], max_size: int) -> list[list[str]]:
    """Split ``items`` into as-equal-as-possible chunks of at most
    ``max_size`` (e.g. 7 items, max_size=3 -> [3, 2, 2], not [3, 3, 1]).
    Opus review WB-JEV-001 Stage 2 R5(b): a lopsided last chunk otherwise
    gets systematically worse cross-chunk-scaled probability mass than its
    peers for no semantic reason."""

    items = list(items)
    n = len(items)
    if n == 0:
        return []
    chunk_count = -(-n // max_size)  # ceil division
    base, extra = divmod(n, chunk_count)
    chunks: list[list[str]] = []
    index = 0
    for i in range(chunk_count):
        size = base + (1 if i < extra else 0)
        chunks.append(items[index : index + size])
        index += size
    return chunks


def _floor_unobserved(mass: dict[str, float]) -> dict[str, float] | None:
    """Opus review WB-JEV-001 Stage 2 R5(d): a label absent from
    top_logprobs is not necessarily impossible -- it may simply have missed
    the server's top-N cutoff. Treating it as an outright 0.0 (the Stage 0/1
    probe's behavior) makes _normalize systematically overconfident in
    whichever label(s) happened to be observed. Give every unobserved label
    half of the chunk's smallest *observed* mass instead. Returns None when
    nothing in the chunk was observed at all -- that chunk contributes no
    signal and every candidate in it must fall back to None, not a
    fabricated uniform distribution."""

    observed = [value for value in mass.values() if value > 0.0]
    if not observed:
        return None
    floor = min(observed) / 2.0
    return {
        label: (value if value > 0.0 else floor)
        for label, value in mass.items()
    }


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

    def score(
        self,
        context_text: str,
        candidates: Sequence[str],
        *,
        max_calls: int | None = None,
    ) -> tuple[list[float | None], int, bool]:
        return [None] * len(candidates), 0, False


class FakeJudge:
    """Deterministic, network-free judge (backend "fake"): p is a sha256
    digest of (context, candidate), so the same pair always yields the same
    value (mirroring Ollama's temperature-0 determinism) without a network
    call. Not used in a real run -- it exists so ``gapengine.evolve``'s
    rationality wiring (cfg resolution, per-generation merge, budget
    accounting) can be exercised end-to-end in tests without Ollama/a GPU
    (Opus review WB-JEV-001 Stage 2 P3/P4 item 4). Treats every call as
    "noul"-shaped (one unit of budget per candidate) regardless of the
    configured method, since it has no chunking concept of its own."""

    backend_name = "fake"
    model = "fake-v1"

    def score(
        self,
        context_text: str,
        candidates: Sequence[str],
        *,
        max_calls: int | None = None,
    ) -> tuple[list[float | None], int, bool]:
        candidates = list(candidates)
        scored = len(candidates) if max_calls is None else min(len(candidates), max_calls)
        truncated = max_calls is not None and scored < len(candidates)
        results: list[float | None] = []
        for index, candidate in enumerate(candidates):
            if index >= scored:
                results.append(None)
                continue
            digest = hashlib.sha256(
                f"{context_text}|{candidate}".encode("utf-8")
            ).hexdigest()
            results.append((int(digest[:8], 16) % 1000) / 1000.0)
        return results, scored, truncated


class OllamaLogprobJudge:
    """Scores candidates against a local Ollama model's yes/no ("noul") or
    multiple-choice ("choice") logprob readout. Never raises: a request
    failure (HTTP error, timeout, or a response missing every expected
    token) yields ``None`` for the affected candidate(s), never an
    exception (WB-JEV-001 Stage 2 plan §1.2).

    ``score`` returns ``(scores, calls_made, truncated)``: ``calls_made`` is
    the number of underlying model calls actually issued (1 per candidate
    for "noul", 1 per chunk for "choice" -- Opus review R3), and
    ``truncated`` is True iff ``max_calls`` cut the run short (some
    candidates never got a chance -- distinct from a candidate that was
    attempted and came back empty).

    ``thermal_guard`` (2026-09-19 addendum, {"max_temp", "cooldown_seconds",
    "check_every"}, or None to disable) checks the GPU temperature every
    ``check_every`` real model calls and sleeps ``cooldown_seconds`` at a
    time (up to 10 rounds) while it stays at or above ``max_temp``, before
    letting the next call through. It only ever adds sleep time -- it
    never touches the rng or changes what gets scored -- so it does not
    affect determinism, only wall-clock time. ``thermal_wait_seconds``
    accumulates across this judge instance's whole lifetime (which may
    span multiple seeds within one run_individual job); Rationality reads
    the delta since its own construction into its per-seed meta."""

    backend_name = "ollama"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 300.0,
        method: str = "noul",
        thermal_guard: Mapping[str, Any] | None = None,
    ) -> None:
        if method not in ("noul", "choice"):
            raise ValueError(f"Unknown rationality judge method: {method}")
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.method = method
        self._chunk_size: int | None = None
        self._thermal_guard = (
            {
                "max_temp": float(thermal_guard.get("max_temp", 82.0)),
                "cooldown_seconds": float(thermal_guard.get("cooldown_seconds", 45.0)),
                "check_every": max(1, int(thermal_guard.get("check_every", 10))),
            }
            if thermal_guard
            else None
        )
        self._calls_since_thermal_check = 0
        self.thermal_wait_seconds = 0.0

    def _maybe_wait_for_thermal_guard(self) -> None:
        guard = self._thermal_guard
        if guard is None:
            return
        self._calls_since_thermal_check += 1
        if self._calls_since_thermal_check < guard["check_every"]:
            return
        self._calls_since_thermal_check = 0
        for _ in range(10):
            temperature = _read_gpu_temperature()
            if temperature is None or temperature < guard["max_temp"]:
                return
            time.sleep(guard["cooldown_seconds"])
            self.thermal_wait_seconds += guard["cooldown_seconds"]

    def score(
        self,
        context_text: str,
        candidates: Sequence[str],
        *,
        max_calls: int | None = None,
    ) -> tuple[list[float | None], int, bool]:
        candidates = list(candidates)
        if not candidates:
            return [], 0, False
        if self.method == "choice":
            return self._score_choice(context_text, candidates, max_calls=max_calls)
        return self._score_noul(context_text, candidates, max_calls=max_calls)

    def _score_noul(
        self,
        context_text: str,
        candidates: list[str],
        *,
        max_calls: int | None,
    ) -> tuple[list[float | None], int, bool]:
        results: list[float | None] = []
        calls_made = 0
        truncated = False
        for desc in candidates:
            if max_calls is not None and calls_made >= max_calls:
                results.append(None)
                truncated = True
                continue
            self._maybe_wait_for_thermal_guard()
            prompt = f"{context_text}\n\n候補: {desc}\n\n{QUESTION}"
            try:
                data = _ollama_call(
                    self.model, prompt, base_url=self.base_url, timeout=self.timeout
                )
            except (OSError, urllib.error.URLError, ValueError):
                results.append(None)
                calls_made += 1
                continue
            calls_made += 1
            p_yes, _top = _p_yes(data)
            results.append(p_yes)
        return results, calls_made, truncated

    def _score_choice(
        self,
        context_text: str,
        candidates: list[str],
        *,
        max_calls: int | None,
    ) -> tuple[list[float | None], int, bool]:
        if len(candidates) == 1:
            return [1.0], 0, False
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

        total = len(candidates)
        chunks = _even_chunks(candidates, self._chunk_size)
        results: list[float | None] = [None] * total
        calls_made = 0
        truncated = False
        index = 0
        for chunk in chunks:
            if max_calls is not None and calls_made >= max_calls:
                truncated = True
                break
            self._maybe_wait_for_thermal_guard()
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
                calls_made += 1
                index += len(chunk)
                continue
            calls_made += 1
            floored = _floor_unobserved(_label_masses(data, labels))
            if floored is not None:
                probs = _normalize(floored)
                # This chunk's masses are only normalized against each
                # other, not the full candidate set (Ollama's top_logprobs
                # window can't score them all in one call). Rescaling by
                # this chunk's share of the whole makes every chunk's
                # output land in the same global scale (they sum to <=1.0
                # across every scored candidate) -- but a candidate's value
                # still isn't the same thing as it would be in one true
                # joint choice over every candidate at once (Opus review
                # R5(c); known limitation, not fixed by this rescaling).
                scale = len(chunk) / total
                for offset, label in enumerate(labels):
                    results[index + offset] = probs[label] * scale
            index += len(chunk)
        return results, calls_made, truncated


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
        self._judge_disabled = False
        self._consecutive_failures = 0
        self._new_entries: dict[str, float] = {}
        self._table_hash_at_start = table.hash
        self._recipe_lines: str | None = None
        self._map_line: str | None = None
        # 2026-09-19 thermal-guard addendum: the judge may be shared across
        # every seed of one run_individual job (so its own
        # thermal_wait_seconds accumulates over the whole job), but each
        # Rationality instance is per-seed -- snapshotting the judge's
        # running total here lets .meta report only *this* seed's share.
        self._thermal_wait_at_start = float(
            getattr(judge, "thermal_wait_seconds", 0.0)
        )

    @property
    def enabled(self) -> bool:
        return self.kappa > 0.0

    @property
    def new_entries(self) -> dict[str, float]:
        return dict(self._new_entries)

    @property
    def meta(self) -> dict[str, Any]:
        """Full bookkeeping, for run_individual's per-seed run_result and
        evolve()'s generation_summary aggregation. The engine.sim header
        shows only the static kappa/method/backend/model/table_hash_at_start
        subset -- judge_calls/budget_exhausted/judge_disabled describe what
        happened *during* the run the header is written before, so they'd
        always read as 0/absent there (Opus review WB-JEV-001 Stage 2 R1)."""

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
        if self._judge_disabled:
            value["judge_disabled"] = True
        thermal_wait = (
            float(getattr(self.judge, "thermal_wait_seconds", 0.0))
            - self._thermal_wait_at_start
        )
        if thermal_wait > 0.0:
            value["thermal_wait_seconds"] = thermal_wait
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
        if missing and self._judge_disabled:
            pass  # circuit open (P1): leave every missing key as None, no call.
        elif missing:
            if self.max_judge_calls is None:
                remaining = None
            else:
                remaining = max(0, self.max_judge_calls - self._judge_calls)
            if remaining is not None and remaining <= 0:
                self._budget_exhausted = True
            else:
                # "choice" scores the whole presented menu (sorted, so the
                # prompt's A/B/C.. label assignment never depends on the
                # incidental order Policy happened to list actions in --
                # Opus review R5(a)); "noul" only needs the still-missing
                # ones, since each candidate is scored independently.
                scored_descs = (
                    sorted(unique_descs) if self.method == "choice" else missing
                )
                scores, calls_made, truncated = self.judge.score(
                    context_text, scored_descs, max_calls=remaining
                )
                self._judge_calls += calls_made
                if truncated:
                    self._budget_exhausted = True

                new_entries = {
                    keys[desc]: p
                    for desc, p in zip(scored_descs, scores)
                    if p is not None
                }
                self.table.update(new_entries)
                self._new_entries.update(new_entries)

                # Circuit breaker (P1): 3 consecutive fully-empty judge
                # calls (every candidate came back None -- a systemic
                # failure, not an individual 欠測) disables the judge for
                # the rest of this run; every later decision point's
                # missing candidates fall straight to None/m_rat=1.0
                # without ever calling the judge again.
                if scores and all(p is None for p in scores):
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= 3:
                        self._judge_disabled = True
                else:
                    self._consecutive_failures = 0

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
