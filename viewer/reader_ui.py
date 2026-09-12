"""Reader-first presentation, with the original explanation behind details."""
from html import escape
from viewer import explanation_ui


def panel(explanation):
    reader = explanation.get("reader_summary")
    if not reader:
        return explanation_ui.panel(explanation)
    summary = reader["summary"]
    body = '<div class="reader-story"><p class="eyebrow">この候補の展開</p>'
    body += '<h2>' + escape(summary["title"]["text"]) + '</h2><p class="reader-prose">'
    body += ''.join(escape(part["text"]) for part in summary["sentences"]) + '</p>'
    body += '<p class="muted reader-label">LLMで文章化・編集と原ログ照合済みの試作</p></div>'
    body += '<details class="reader-evidence"><summary>根拠と詳細を見る（四項目・原ログ）</summary>'
    facts = {fact["id"]: fact for fact in reader["packet"]["facts"]}
    for part in [summary["title"], *summary["sentences"]]:
        lines = sorted({line for ref in part["refs"] for line in facts[ref]["lines"]})
        links = ' '.join(f'<a href="{explanation_ui.base_url(explanation)}/raw?line={n}#L{n}">L{n}</a>' for n in lines)
        body += '<p>' + escape(part["text"]) + ' ' + links + '</p>'
    body += '<p class="muted">生成: ' + escape(str(reader["model"])) + ' / 照合: ' + escape(str(reader["reviewer"])) + '</p>'
    return body + explanation_ui.panel(explanation) + '</details>'


def short(explanation):
    reader = explanation.get("reader_summary")
    if not reader:
        return explanation_ui.short(explanation)
    return ('<div class="reader-short"><span class="reader-short-label">LLM要約・編集照合済み</span>'
            '<p><strong>' + escape(reader["summary"]["title"]["text"]) + '</strong></p></div>'
            + explanation_ui.short(explanation))
