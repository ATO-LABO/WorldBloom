"""Reader-first presentation, with the original explanation behind details."""
from html import escape
from viewer import explanation_ui


def _label(reader):
    # Default to the unreviewed label when "reviewed" is missing entirely --
    # never let a caller that forgot the flag get mistaken for a human-
    # approved summary.
    if reader.get("reviewed", False):
        return "LLMで文章化・編集と原ログ照合済みの試作"
    return "AI生成（未照合）。根拠は下の原ログで確かめられます"


def _short_label(reader):
    return "LLM要約・編集照合済み" if reader.get("reviewed", False) else "AI生成・未照合"


# Labels match explanation_ui.panel()'s raw four-item <dl> exactly, so the
# LLM-organized version and the raw version read as the same four items
# (status tags like 確認済み/該当なし are the extractor's own judgement, not
# an attribute of the LLM's sentence, so they're not repeated here).
_ITEM_LABELS = (("choice", "選択"), ("grounds", "根拠"), ("cost", "即時の代償"), ("turning", "転機"))


def panel(explanation):
    reader = explanation.get("reader_summary")
    if not reader:
        return explanation_ui.panel(explanation)
    summary = reader["summary"]
    facts = {fact["id"]: fact for fact in reader["packet"]["facts"]}
    base = explanation_ui.base_url(explanation)

    def evidence_links(part):
        lines = sorted({line for ref in part["refs"] for line in facts[ref]["lines"]})
        return ' '.join(f'<a href="{base}/raw?line={n}#L{n}">L{n}</a>' for n in lines)

    body = '<div class="reader-story"><p class="eyebrow">この候補の展開</p>'
    body += '<h2>' + escape(summary["title"]["text"]) + '</h2>'
    body += '<dl class="four-items">'
    for key, label in _ITEM_LABELS:
        part = summary.get(key)
        if part:
            body += f'<dt>{label}</dt><dd>{escape(part["text"])}</dd>'
        else:
            body += f'<dt>{label}</dt><dd class="muted">記録なし</dd>'
    body += '</dl>'
    body += '<p class="eyebrow">この候補のあらすじ</p><p class="reader-prose">'
    body += ''.join(escape(part["text"]) for part in summary["synopsis"]) + '</p>'
    body += '<p class="muted reader-label">' + escape(_label(reader)) + '</p></div>'
    body += '<details class="reader-evidence"><summary>根拠と詳細を見る（四項目・原ログ）</summary>'
    body += '<p><strong>見出し:</strong> ' + escape(summary["title"]["text"]) + ' ' + evidence_links(summary["title"]) + '</p>'
    for key, label in _ITEM_LABELS:
        part = summary.get(key)
        if part:
            body += f'<p><strong>{label}:</strong> ' + escape(part["text"]) + ' ' + evidence_links(part) + '</p>'
    for index, part in enumerate(summary["synopsis"], 1):
        body += f'<p><strong>あらすじ{index}:</strong> ' + escape(part["text"]) + ' ' + evidence_links(part) + '</p>'
    reviewer = escape(str(reader["reviewer"])) if reader.get("reviewer") else "未照合"
    body += '<p class="muted">生成: ' + escape(str(reader["model"])) + ' / 照合: ' + reviewer + '</p>'
    return body + explanation_ui.panel(explanation) + '</details>'


def short(explanation):
    reader = explanation.get("reader_summary")
    if not reader:
        return explanation_ui.short(explanation)
    return ('<div class="reader-short"><span class="reader-short-label">' + escape(_short_label(reader)) + '</span>'
            '<p><strong>' + escape(reader["summary"]["title"]["text"]) + '</strong></p></div>'
            + explanation_ui.short(explanation))


def generate_button(experiment_name, cell_key, *, url_segment):
    """WB-EXPLAIN-009: shown wherever a candidate has no reader_summary yet
    (compare/cell-detail callers gate this on control mode + a configured
    backend; the server route re-checks both)."""
    endpoint = f"/exp/{url_segment(experiment_name)}/cell/{url_segment(cell_key)}/reader-summary"
    return (
        f'<button type="button" class="button reader-generate" data-endpoint="{escape(endpoint, quote=True)}">'
        "この候補をLLMで読みやすく整理する</button>"
        '<p class="muted">⚙設定の文章生成バックエンドを1回呼びます。結果はAI生成・未照合として表示されます。</p>'
    )
