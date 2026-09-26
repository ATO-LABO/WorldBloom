"""WB-JEV-005 tests for JevJudge (gapengine/rationality.py) and its wiring
into gapengine.evolve._build_rationality_judge/_rationality_lease_params.
Network-free throughout: gapengine.rationality._jev_call is patched, never
the real TypeSafe API."""

from __future__ import annotations

import http.client
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import call, patch

from gapengine.evolve import _build_rationality_judge, _rationality_lease_params
from gapengine.rationality import (
    JEV_DEFAULT_MODEL,
    JEV_ZERO_FLOOR,
    JevJudge,
    Rationality,
    RationalityTable,
    TableOnlyJudge,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "momotaro"


class JevJudgeChoiceTests(unittest.TestCase):
    def test_choice_scores_from_probabilities(self) -> None:
        judge = JevJudge(api_key="k", method="choice")
        data = {
            "model": JEV_DEFAULT_MODEL,
            "answers": {"q": {"type": "choice", "choice": "1", "probabilities": {"1": 0.7, "2": 0.3}}},
        }
        with patch("gapengine.rationality._jev_call", return_value=data) as mock_call:
            scores, calls_made, truncated = judge.score("ctx", ["候補A", "候補B"])
        self.assertEqual(scores, [0.7, 0.3])
        self.assertEqual(calls_made, 1)
        self.assertFalse(truncated)
        payload = mock_call.call_args.args[0]
        self.assertEqual(payload["model"], JEV_DEFAULT_MODEL)
        self.assertEqual(payload["state"], "ctx")
        criteria = payload["questions"]["q"]["criteria"]
        self.assertEqual(criteria, {"1": "候補A", "2": "候補B"})

    def test_single_candidate_never_calls(self) -> None:
        judge = JevJudge(api_key="k", method="choice")
        with patch("gapengine.rationality._jev_call") as mock_call:
            scores, calls_made, truncated = judge.score("ctx", ["唯一の候補"])
        self.assertEqual(scores, [1.0])
        self.assertEqual(calls_made, 0)
        self.assertFalse(truncated)
        mock_call.assert_not_called()

    def test_256_candidates_split_into_two_chunked_calls(self) -> None:
        judge = JevJudge(api_key="k", method="choice")
        candidates = [f"候補{i}" for i in range(256)]

        def fake_call(payload, **_kwargs):
            criteria = payload["questions"]["q"]["criteria"]
            n = len(criteria)
            return {"answers": {"q": {"probabilities": {key: 1.0 / n for key in criteria}}}}

        with patch("gapengine.rationality._jev_call", side_effect=fake_call):
            scores, calls_made, truncated = judge.score("ctx", candidates)
        self.assertEqual(calls_made, 2)
        self.assertEqual(len(scores), 256)
        self.assertFalse(truncated)
        total = sum(value for value in scores if value is not None)
        self.assertLessEqual(total, 1.0 + 1e-9)

    def test_missing_option_key_gets_the_zero_floor(self) -> None:
        """A missing/0.00 option is floored to JEV_ZERO_FLOOR (never a hard
        ban), then the chunk is renormalized."""
        judge = JevJudge(api_key="k", method="choice")
        data = {"answers": {"q": {"probabilities": {"1": 0.9}}}}  # "2" absent
        with patch("gapengine.rationality._jev_call", return_value=data):
            scores, _calls, _truncated = judge.score("ctx", ["a", "b"])
        norm = 0.9 + JEV_ZERO_FLOOR
        self.assertAlmostEqual(scores[0], 0.9 / norm)
        self.assertAlmostEqual(scores[1], JEV_ZERO_FLOOR / norm)
        self.assertGreater(scores[1], 0.0)

    def test_noul_zero_yes_gets_the_zero_floor(self) -> None:
        judge = JevJudge(api_key="k", method="noul")
        data = {"answers": {"1": {"probabilities": {"yes": 0.0, "no": 1.0}}}}
        with patch("gapengine.rationality._jev_call", return_value=data):
            scores, _calls, _truncated = judge.score("ctx", ["a"])
        self.assertEqual(scores, [JEV_ZERO_FLOOR])

    def test_all_options_missing_yields_none_for_whole_chunk(self) -> None:
        judge = JevJudge(api_key="k", method="choice")
        data = {"answers": {"q": {"probabilities": {}}}}
        with patch("gapengine.rationality._jev_call", return_value=data):
            scores, _calls, _truncated = judge.score("ctx", ["a", "b"])
        self.assertEqual(scores, [None, None])

    def test_malformed_probability_values_are_treated_as_missing(self) -> None:
        """NICE 7 (Opus review): a bool, NaN/inf, a string, or an
        out-of-range number must never be accepted as a probability -- each
        is treated the same as "key absent" (0.0 for the other candidates
        in the chunk, since "1" here is genuinely a valid 0.4)."""
        judge = JevJudge(api_key="k", method="choice")
        data = {"answers": {"q": {"probabilities": {
            "1": 0.4, "2": True, "3": float("nan"), "4": float("inf"), "5": "0.9", "6": 1.5,
        }}}}
        with patch("gapengine.rationality._jev_call", return_value=data):
            scores, _calls, _truncated = judge.score("ctx", ["a", "b", "c", "d", "e", "f"])
        norm = 0.4 + 5 * JEV_ZERO_FLOOR
        self.assertAlmostEqual(scores[0], 0.4 / norm)
        for score in scores[1:]:
            self.assertAlmostEqual(score, JEV_ZERO_FLOOR / norm)


class JevJudgeNoulTests(unittest.TestCase):
    def test_noul_bundles_every_candidate_into_one_call(self) -> None:
        judge = JevJudge(api_key="k", method="noul")
        data = {
            "answers": {
                "1": {"probabilities": {"yes": 0.9, "no": 0.1}},
                "2": {"probabilities": {"yes": 0.2, "no": 0.8}},
            }
        }
        with patch("gapengine.rationality._jev_call", return_value=data) as mock_call:
            scores, calls_made, truncated = judge.score("ctx", ["a", "b"])
        self.assertEqual(scores, [0.9, 0.2])
        self.assertEqual(calls_made, 1)
        self.assertFalse(truncated)
        self.assertEqual(mock_call.call_count, 1)

    def test_max_calls_zero_skips_the_request_entirely(self) -> None:
        judge = JevJudge(api_key="k", method="noul")
        with patch("gapengine.rationality._jev_call") as mock_call:
            scores, calls_made, truncated = judge.score("ctx", ["a", "b"], max_calls=0)
        self.assertEqual(scores, [None, None])
        self.assertEqual(calls_made, 0)
        self.assertTrue(truncated)
        mock_call.assert_not_called()


class JevJudgeRetryTests(unittest.TestCase):
    def test_401_returns_none_without_retry(self) -> None:
        judge = JevJudge(api_key="bad", method="noul")
        error = urllib.error.HTTPError("url", 401, "unauthorized", None, None)
        with patch("gapengine.rationality._jev_call", side_effect=error) as mock_call, \
                patch("gapengine.rationality.time.sleep") as mock_sleep:
            scores, _calls, _truncated = judge.score("ctx", ["a", "b"])
        self.assertEqual(scores, [None, None])
        self.assertEqual(mock_call.call_count, 1)
        mock_sleep.assert_not_called()

    def test_429_sleeps_retry_after_then_succeeds(self) -> None:
        judge = JevJudge(api_key="k", method="noul")
        error = urllib.error.HTTPError("url", 429, "too many", {"Retry-After": "5"}, None)
        success = {"answers": {"1": {"probabilities": {"yes": 1.0, "no": 0.0}}}}
        with patch("gapengine.rationality._jev_call", side_effect=[error, success]), \
                patch("gapengine.rationality.time.sleep") as mock_sleep:
            scores, _calls, _truncated = judge.score("ctx", ["a"])
        self.assertEqual(scores, [1.0])
        mock_sleep.assert_called_once_with(5.0)

    def test_urlerror_exhausts_three_attempts_then_none(self) -> None:
        judge = JevJudge(api_key="k", method="noul")
        with patch(
            "gapengine.rationality._jev_call",
            side_effect=urllib.error.URLError("boom"),
        ) as mock_call, patch("gapengine.rationality.time.sleep"):
            scores, _calls, _truncated = judge.score("ctx", ["a"])
        self.assertEqual(scores, [None])
        self.assertEqual(mock_call.call_count, 3)

    def test_429_with_non_numeric_retry_after_falls_back_to_2_seconds(self) -> None:
        """SHOULD 5b (Opus review): a malformed/non-numeric Retry-After must
        never crash the retry loop -- fall back to the same 2s default used
        when the header is absent."""
        judge = JevJudge(api_key="k", method="noul")
        error = urllib.error.HTTPError("url", 429, "too many", {"Retry-After": "soon"}, None)
        success = {"answers": {"1": {"probabilities": {"yes": 1.0, "no": 0.0}}}}
        with patch("gapengine.rationality._jev_call", side_effect=[error, success]), \
                patch("gapengine.rationality.time.sleep") as mock_sleep:
            scores, _calls, _truncated = judge.score("ctx", ["a"])
        self.assertEqual(scores, [1.0])
        mock_sleep.assert_called_once_with(2.0)

    def test_5xx_backs_off_1s_then_2s(self) -> None:
        """SHOULD 5c (Opus review): a non-429 HTTPError (e.g. 500/503) backs
        off 1s, then 2s, then gives up on the third attempt without a
        further sleep."""
        judge = JevJudge(api_key="k", method="noul")
        error = urllib.error.HTTPError("url", 503, "service unavailable", None, None)
        with patch("gapengine.rationality._jev_call", side_effect=error) as mock_call, \
                patch("gapengine.rationality.time.sleep") as mock_sleep:
            scores, _calls, _truncated = judge.score("ctx", ["a"])
        self.assertEqual(scores, [None])
        self.assertEqual(mock_call.call_count, 3)
        mock_sleep.assert_has_calls([call(1.0), call(2.0)])
        self.assertEqual(mock_sleep.call_count, 2)

    def test_http_client_exception_is_retried_like_urlerror(self) -> None:
        """SHOULD 2 (Opus review): http.client.HTTPException (e.g.
        IncompleteRead) is not an OSError/URLError subclass -- it needs its
        own explicit catch, or a truncated/garbled response would propagate
        as an unhandled exception instead of yielding None for the
        candidate like every other transient failure does."""
        judge = JevJudge(api_key="k", method="noul")
        success = {"answers": {"1": {"probabilities": {"yes": 1.0, "no": 0.0}}}}
        with patch(
            "gapengine.rationality._jev_call",
            side_effect=[http.client.IncompleteRead(b""), success],
        ), patch("gapengine.rationality.time.sleep") as mock_sleep:
            scores, _calls, _truncated = judge.score("ctx", ["a"])
        self.assertEqual(scores, [1.0])
        mock_sleep.assert_called_once_with(1.0)


class RationalityMetaTests(unittest.TestCase):
    def test_meta_omits_thermal_and_num_ctx_and_names_jev_backend(self) -> None:
        table = RationalityTable()
        judge = JevJudge(api_key="k", method="noul")
        rationality = Rationality(kappa=0.5, table=table, judge=judge)
        meta = rationality.meta
        self.assertEqual(meta["backend"], "jev")
        self.assertNotIn("thermal_wait_seconds", meta)
        self.assertNotIn("num_ctx", meta)


class BuildRationalityJudgeTests(unittest.TestCase):
    def test_table_only_jev_never_touches_the_network(self) -> None:
        judge = _build_rationality_judge({"backend": "jev", "model": "jev-1.13.0"}, table_only=True)
        self.assertIsInstance(judge, TableOnlyJudge)
        self.assertEqual(judge.backend_name, "jev")
        self.assertEqual(judge.model, "jev-1.13.0")
        with self.assertRaises(Exception):
            judge.score("ctx", ["a", "b"])

    def test_live_jev_judge_defaults_to_the_pinned_model(self) -> None:
        judge = _build_rationality_judge({"backend": "jev"})
        self.assertIsInstance(judge, JevJudge)
        self.assertEqual(judge.model, JEV_DEFAULT_MODEL)


class RationalityLeaseParamsTests(unittest.TestCase):
    def test_jev_backend_never_requests_the_gpu_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "template": str(TEMPLATE),
                "out": tmp,
                "rationality": {"kappa": 0.5, "backend": "jev"},
            }
            needs_lease, _owner, _wait = _rationality_lease_params(cfg)
        self.assertFalse(needs_lease)


if __name__ == "__main__":
    unittest.main()
