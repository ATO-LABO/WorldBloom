"""Read-only record indexing. Original bytes and physical line numbers are authoritative."""
from dataclasses import dataclass
import hashlib
import json
import math
from collections.abc import Mapping
from viewer import data, explanation_ui

PAGE_SIZE = 100
KINDS = {"header": "実行の情報", "decision": "行動", "event": "出来事",
         "snapshot": "状態", "ending": "結末", "summary": "結果"}
EVENTS = {"encounters": "出会い", "ending": "結末"}


def scalar(value):
    return str(value) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def mapping(value):
    return value if isinstance(value, dict) else {}


def sequence(value):
    return value if isinstance(value, list) else []


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("項目名が重複しています")
        obj[key] = value
    return obj


def _bad_constant(value):
    raise ValueError("JSONの数値として扱えない値です")


def _finite_float(token):
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("数値が表示範囲外です")
    return value


def parse_record(raw):
    if not raw.strip():
        return None, "空行"
    try:
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=_bad_constant, parse_float=_finite_float), None
    except (ValueError, RecursionError):
        return None, "JSONとして整理できない行です。生ログで原文を確認できます。"


def action_label(row):
    verb = scalar(row.get("verb"))
    return explanation_ui.VERBS.get(verb, EVENTS.get(verb, verb or "種類の記録なし"))


def title(row, error=None):
    if error:
        return "空行" if error == "空行" else "読み取りを確認する記録"
    if not isinstance(row, dict):
        return "値の記録"
    subject, verb = scalar(row.get("subject")), scalar(row.get("verb"))
    args = sequence(row.get("args"))
    if verb == "give_item" and subject and len(args) >= 2 and row.get("effective") is True:
        return f"{subject}が{scalar(args[0])}に{scalar(args[1])}を渡した"
    if verb:
        text = f"{subject}：{action_label(row)}" if subject else action_label(row)
        return text + ("（" + "・".join(scalar(a) or "…" for a in args) + "）" if args else "")
    kind = scalar(row.get("kind"))
    return KINDS.get(kind, kind or "項目の記録")


def people(row):
    row = mapping(row)
    result = set()
    for v in (row.get("subject"), row.get("target"), row.get("protagonist"),
              row.get("antagonist"), mapping(row.get("details")).get("target")):
        if scalar(v):
            result.add(scalar(v))
    for v in sequence(mapping(row.get("explanation")).get("present")):
        if scalar(v):
            result.add(scalar(v))
    for relation in sequence(mapping(row.get("delta")).get("relations")):
        for key in ("observer", "target"):
            v = mapping(relation).get(key)
            if scalar(v):
                result.add(scalar(v))
    return sorted(result)


@dataclass
class Record:
    line: int
    raw: str
    value: object
    error: str | None

    @property
    def row(self):
        return mapping(self.value)

    @property
    def kind(self):
        if self.error:
            return "blank" if self.error == "空行" else "invalid"
        return scalar(self.row.get("kind")) or "other"

    @property
    def kind_label(self):
        return KINDS.get(self.kind, {"blank": "空行", "invalid": "要確認", "other": "その他"}.get(self.kind, self.kind))

    @property
    def title(self):
        return title(self.value, self.error)

    @property
    def group(self):
        if self.kind == "header":
            return "開始時の設定"
        day, slot = scalar(self.row.get("day")), scalar(self.row.get("slot"))
        return (f"{day}日目" + ("・" + slot if slot else "")) if day else "日時の記録なし"


def read_source(repository, experiment_name, cell_key):
    experiment = repository.experiment(experiment_name)
    repository.validate_segment(cell_key)
    elite = mapping(repository.archive(experiment).get("cells")).get(cell_key)
    if not isinstance(elite, Mapping):
        raise data.MissingResource("candidate not found")
    exemplar = mapping(elite.get("exemplar"))
    relative = exemplar.get("layers_path")
    if not isinstance(relative, str):
        raise data.MissingResource("exemplar log not recorded")
    path = repository.safe_path(experiment, relative)
    if not path.is_file():
        raise data.MissingResource("exemplar log not found")
    payload = path.read_bytes()
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise data.BadRequest("原記録をUTF-8として読み取れません") from exc
    # Only LF/CRLF are JSONL record boundaries; U+2028 inside a JSON string is data.
    raw_lines = text.split("\n") if text else []
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()
    records = []
    for number, raw in enumerate(raw_lines, 1):
        if raw.endswith("\r"):
            raw = raw[:-1]
        value, error = parse_record(raw)
        records.append(Record(number, raw, value, error))
    header = next((r.row for r in records if r.kind == "header"), {})
    return {"records": records, "sha256": hashlib.sha256(payload).hexdigest(),
            "relative": relative, "size": len(payload), "world": scalar(header.get("world")),
            "generation": elite.get("generation"), "seed": exemplar.get("seed", header.get("seed")),
            "experiment": experiment_name, "cell": cell_key}


def select(source, *, line=None, q="", kind="", person="", page=None, expected_source=None):
    records = source["records"]
    if expected_source and expected_source != source["sha256"]:
        return {"changed": True}
    number = None
    if line is not None:
        try:
            # Keep the old raw?line=N contract; do not accept fractions or booleans.
            value = str(line)
            if not value.isascii() or not value.isdecimal():
                raise ValueError
            number = int(value)
        except (ValueError, TypeError):
            raise data.BadRequest("行番号は1以上の整数で指定してください") from None
        if not 1 <= number <= len(records):
            raise data.BadRequest("行番号が原記録の範囲外です")
    search = q.strip().casefold()
    matches = [r for r in records if
               (not search or search in (r.raw + " " + r.title + " " + r.kind_label).casefold())
               and (not kind or r.kind == kind)
               and (not person or person in people(r.value))]
    selected = records[number-1] if number else (matches[0] if matches else None)
    position = next((i for i, r in enumerate(matches) if selected and r.line == selected.line), None)
    total_pages = max(1, (len(matches) + PAGE_SIZE-1)//PAGE_SIZE)
    if page is None:
        current_page = (position // PAGE_SIZE + 1) if position is not None else 1
    else:
        try:
            current_page = max(1, min(total_pages, int(page)))
        except (TypeError, ValueError):
            current_page = 1
    start = (current_page-1)*PAGE_SIZE
    return {"changed": False, "matches": len(matches), "selected": selected,
            "outside": selected is not None and position is None,
            "entries": matches[start:start+PAGE_SIZE], "page": current_page, "pages": total_pages,
            "first": start+1 if matches else 0, "last": min(start+PAGE_SIZE, len(matches)),
            "previous": matches[position-1].line if position is not None and position > 0 else None,
            "next": matches[position+1].line if position is not None and position+1 < len(matches) else None}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
