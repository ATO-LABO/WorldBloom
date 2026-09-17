"""Reader-first presentation, with the original explanation behind details."""
from html import escape
from viewer import explanation_ui


def _label(reader):
    if reader.get("reviewed", True):
        return "LLMで文章化・編集と原ログ照合済みの試作"
    return "AI生成（未照合）。根拠は下の原ログで確かめられます"


def _short_label(reader):
    return "LLM要約・編集照合済み" if reader.get("reviewed", True) else "AI生成・未照合"


def panel(explanation):
    reader = explanation.get("reader_summary")
    if not reader:
        return explanation_ui.panel(explanation)
    summary = reader["summary"]
    body = '<div class="reader-story"><p class="eyebrow">この候補の展開</p>'
    body += '<h2>' + escape(summary["title"]["text"]) + '</h2><p class="reader-prose">'
    body += ''.join(escape(part["text"]) for part in summary["sentences"]) + '</p>'
    body += '<p class="muted reader-label">' + escape(_label(reader)) + '</p></div>'
    body += '<details class="reader-evidence"><summary>根拠と詳細を見る（四項目・原ログ）</summary>'
    facts = {fact["id"]: fact for fact in reader["packet"]["facts"]}
    for part in [summary["title"], *summary["sentences"]]:
        lines = sorted({line for ref in part["refs"] for line in facts[ref]["lines"]})
        links = ' '.join(f'<a href="{explanation_ui.base_url(explanation)}/raw?line={n}#L{n}">L{n}</a>' for n in lines)
        body += '<p>' + escape(part["text"]) + ' ' + links + '</p>'
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
