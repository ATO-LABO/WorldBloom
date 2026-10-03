"""Structural checks aid review; references never prove semantic fidelity."""
import json
import re
from execution.provenance import canonical, sha256

VERSION = "story-validation-5"

def inspect_story(raw_text, material, plan):
    findings = []
    text, refs, coverage = None, [], set()
    story_level = False
    try:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
        doc = json.loads(cleaned)
        if isinstance(doc,dict):
            if isinstance(doc.get("story"),str):text=doc["story"]
            elif isinstance(doc.get("text"),str):text=doc["text"]
            elif isinstance(doc.get("paragraphs"),list):
                readable=[p.get("text") for p in doc["paragraphs"] if isinstance(p,dict) and isinstance(p.get("text"),str)]
                if readable:text="\n\n".join(readable)
        story_level = isinstance(doc,dict) and set(doc)=={"story","used_event_ids"}
        if story_level:
            doc={"text":doc["story"],"paragraph_sources":[{"paragraph":0,"event_ids":doc["used_event_ids"],"embellishments":[]}]}
        if isinstance(doc,dict) and set(doc)=={"paragraphs"}:
            paragraphs=doc["paragraphs"]
            if not isinstance(paragraphs,list) or not paragraphs:
                raise ValueError("空でない段落配列が必要です")
            if any(not isinstance(p,dict) or set(p)!={"text","event_ids","embellishments"}
                   or not isinstance(p["text"],str) or not p["text"].strip()
                   or re.search(r"\n\s*\n",p["text"]) for p in paragraphs):
                raise ValueError("各段落に本文と根拠と補完を指定してください")
            doc={"text":"\n\n".join(p["text"].strip() for p in paragraphs),
                 "paragraph_sources":[{"paragraph":n,"event_ids":p["event_ids"],"embellishments":p["embellishments"]}
                                      for n,p in enumerate(paragraphs,1)]}
        if not isinstance(doc,dict) or set(doc)!={"text","paragraph_sources"}:
            raise ValueError("本文と段落参照のオブジェクトが必要です")
        text,refs=doc["text"],doc["paragraph_sources"]
        if not isinstance(text,str) or not text.strip() or not isinstance(refs,list):
            raise ValueError("本文・段落参照の型が不正です")
        paragraphs=[p.strip() for p in re.split(r"\n\s*\n",text.strip()) if p.strip()]
        if ((not story_level and (len(refs)!=len(paragraphs) or [r.get("paragraph") for r in refs]!=list(range(1,len(paragraphs)+1))))
                or any(not isinstance(r,dict) for r in refs)):
            raise ValueError("全段落の参照を順に指定してください")
        aliases={"r"+str(e["order"]):e["event_id"] for e in material["events"]}
        planned={i for b in plan["beats"] for i in b["event_ids"]}
        for r in refs:
            if isinstance(r.get("event_ids"),list):
                r["event_ids"]=[aliases.get(i,i) if isinstance(i,str) else i for i in r["event_ids"]]
            if (not isinstance(r.get("event_ids"),list) or not r["event_ids"]
                or any(not isinstance(i,str) or i not in planned for i in r["event_ids"])
                or not isinstance(r.get("embellishments"),list) or any(not isinstance(x,str) for x in r["embellishments"])):
                raise ValueError("構成内の根拠IDと補完内容を指定してください")
            coverage.update(r["event_ids"])
        missing=sorted(set(plan["required_event_ids"])-coverage)
        if missing:findings.append({"severity":"error","kind":"missing_required_refs","event_ids":missing,"message":"必須イベントへの参照が欠落"})
        # References in prose must not move a later event before an earlier one.
        byid={e["event_id"]:e["order"] for e in material["events"]}
        maxima=[max(byid[i] for i in r["event_ids"]) for r in refs]
        if maxima!=sorted(maxima):findings.append({"severity":"error","kind":"reference_order","message":"段落参照の時系列が逆転しています"})
        if "log:" in text or re.search(r"\bturn\s*\d+",text,re.I):
            findings.append({"severity":"error","kind":"internal_metadata","message":"内部記録が本文に含まれています"})
        if re.search(r"主目標達成|記録上|記録されて|機械的に|アルゴリズム",text):
            findings.append({"severity":"warning","kind":"reader_metacomment","message":"本文に記録や実装の解説が含まれる可能性があります"})
        if "焚き火" in text and not any(e["verb"]=="payoff" and e["details"].get("library_id")=="campfire_oath" for e in material["events"]):
            findings.append({"severity":"warning","kind":"unsupported_campfire","message":"焚き火の根拠を人が確認してください"})
    except (ValueError,TypeError,KeyError,AttributeError) as error:
        findings.append({"severity":"error","kind":"invalid_structure","message":str(error)})
        if not isinstance(text,str) or not text.strip():text=None
        refs,coverage=[],set()
    record={"schema_version":1,"validator_version":VERSION,"raw_text_sha256":sha256(raw_text.encode("utf-8")),
            "reference_encoding":"row_alias_story_v2" if story_level else "row_alias_v1","text_sha256":sha256(text.encode("utf-8")) if text is not None else None,
            "material_sha256":material["content_sha256"],"plan_sha256":sha256(canonical(plan)),
            "structural_status":"failed" if any(f["severity"]=="error" for f in findings) else "passed",
            "findings":findings,"required_event_coverage":sorted(coverage),"semantic_review_status":"not_reviewed",
            "human_adoption":"pending","limitation":"参照の存在は、本文がその事実を正しく描いた証明ではありません"}
    return {"text":text,"paragraph_sources":refs,"validation":record}
