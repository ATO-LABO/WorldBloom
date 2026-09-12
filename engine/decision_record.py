"""Read-only, bounded recording of the distribution actually sampled."""
from __future__ import annotations

VERSION = 1
RULE = "top_weight_8_plus_selected;ties=candidate_index"


def record_distribution(weighted, weights, selected_index, *, fallback=None):
    """Never sort the input, consume randomness, or expose action metadata."""
    total = sum(weights)
    indices = sorted(range(len(weighted)), key=lambda i: (-weights[i], i))[:8]
    if selected_index is not None and selected_index not in indices:
        indices.append(selected_index)
    items = []
    for i in indices:
        action = weighted[i][0]
        items.append({"candidate_index": i, "verb": action.verb,
                      "args": list(action.args), "weight": weights[i],
                      "probability": weights[i] / total if total > 0 else None,
                      "selected": i == selected_index})
    return {"version": VERSION, "rule": RULE, "total_candidates": len(weighted),
            "recorded_candidates": len(items), "truncated": len(items) < len(weighted),
            "total_weight": total, "selected_index": selected_index,
            "fallback": fallback, "candidates": items}
