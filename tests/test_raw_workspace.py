"""Original-record fidelity, bounded rendering and source-link contracts."""
import hashlib
import html
from html.parser import HTMLParser
import json
import re
from pathlib import Path
import tempfile
import threading
import shutil
import subprocess
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
from viewer import data, pages, raw_view, raw_pages, server


def rows():
    return [
        {"kind":"header","world":"放課後の約束","seed":1,"protagonist":"A","antagonist":"C"},
        {"kind":"event","turn":1,"day":1,"slot":"朝","verb":"encounters","subject":None,
         "explanation":{"present":["A","B"]}},
        {"kind":"decision","turn":1,"day":1,"slot":"朝","subject":"A","verb":"give_item",
         "args":["B","花束"],"details":{"target":"B","item":"花束"},"effective":True,
         "delta":{"relations":[{"observer":"A","target":"B","affinity":0.08}]}},
        {"kind":"snapshot","day":1,"agents":{"A":{"stamina":13}}},
        {"kind":"future","day":2,"subject":"C","value":"unknown"},
    ]


def create_fixture(root, values=None):
    exp=root/"exp1"
    log=exp/"g4"/"ind-1"/"seed-1"/"layers.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("".join(json.dumps(v,ensure_ascii=False)+"\n" for v in (rows() if values is None else values)),encoding="utf-8")
    (exp/"archive.json").write_text(json.dumps({"cells":{"II|mid":{"generation":4,"exemplar":{"seed":1,"layers_path":"g4/ind-1/seed-1/layers.jsonl"}}}}),encoding="utf-8")
    return log


class Original(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.active=False
        self.text=""
        self.feed(value)
    def handle_starttag(self, tag, attrs):
        if tag=="code" and "data-rv-original" in dict(attrs):
            self.active=True
    def handle_endtag(self, tag):
        if tag=="code":
            self.active=False
    def handle_data(self, text):
        if self.active:
            self.text+=text


class RawWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.log=create_fixture(self.root)
        self.repo=data.RunRepository(self.root)

    def source(self):
        return raw_view.read_source(self.repo,"exp1","II|mid")

    def render(self, **options):
        return pages.raw_page(self.repo,"exp1","II|mid",**options)

    def test_original_bytes_hash_whitespace_and_line_boundaries(self):
        payload=b"\xef\xbb\xbf"+(' { "text" : "a\u2028b & <tag>" }\r\n\r\n{"value":2}\r\n').encode("utf-8")
        self.log.write_bytes(payload)
        source=self.source()
        self.assertEqual(source["sha256"],hashlib.sha256(payload).hexdigest())
        self.assertEqual(len(source["records"]),3)
        self.assertEqual(source["records"][1].kind,"blank")
        page=self.render(line=1,mode="json")
        self.assertEqual(Original(page).text,' { "text" : "a\u2028b & <tag>" }')
        self.assertNotIn("<tag>",page)
        self.assertEqual(self.log.read_bytes(),payload)

    def test_copy_payload_preserves_control_characters_and_cannot_close_script(self):
        raw = "bad" + chr(13) + chr(0) + "</script><script>alert(1)</script>"
        self.log.write_bytes((raw + chr(10)).encode("utf-8"))
        page=self.render(line=1,mode="json")
        payload=re.search(r'<script type="application/json" data-rv-copy-source>(.*?)</script>',page).group(1)
        self.assertEqual(json.loads(payload),raw)
        self.assertNotIn("<script>alert",page)

    def test_filters_retain_original_line_numbers_and_involved_people(self):
        source=self.source()
        filtered=raw_view.select(source,q="花束",kind="decision",person="B")
        self.assertEqual([r.line for r in filtered["entries"]],[3])
        self.assertEqual(raw_view.select(source,person="花束")["matches"],0)
        self.assertIn("A",raw_view.people(source["records"][2].value))

    def test_selected_line_outside_filters_is_explicit(self):
        result=raw_view.select(self.source(),line=3,kind="header")
        self.assertTrue(result["outside"])
        self.assertIsNone(result["previous"])
        self.assertIn("絞り込みの対象外",self.render(line=3,kind="header"))

    def test_old_deep_link_selects_exact_line(self):
        page=self.render(line=3)
        self.assertIn('id="L3"',page)
        self.assertIn('data-line="3"',page)
        self.assertIn('aria-current="true"',page)
        self.assertIn("AがBに花束を渡した",page)
        self.assertEqual(Original(page).text,self.log.read_text(encoding="utf-8").splitlines()[2])

    def test_invalid_lines_rejected(self):
        for value in (0,-1,6,"x","2.5",True,"１２"):
            with self.subTest(value=value),self.assertRaises(data.BadRequest):
                self.render(line=value)

    def test_stale_source_is_not_silently_rebound(self):
        old=self.source()["sha256"]
        self.log.write_text('{"kind":"header","world":"別の世界"}\n',encoding="utf-8")
        page=self.render(line=3,expected_source=old)
        self.assertIn("原記録が更新されています",page)
        self.assertNotIn("data-rv-original",page)
        self.assertNotIn('class="rv-records"',page)

    def test_large_log_has_bounded_list_and_selected_record_only(self):
        values=[{"kind":"event","turn":i,"subject":"A","verb":"observe"} for i in range(20001)]
        self.log.write_text("".join(json.dumps(v)+"\n" for v in values),encoding="utf-8")
        source=self.source()
        state=raw_view.select(source,line=20001)
        self.assertEqual(state["page"],201)
        self.assertEqual(state["previous"],20000)
        self.assertIsNone(state["next"])
        page=raw_pages.render_source(source,line=101)
        self.assertEqual(page.count('class="rv-line"'),100)
        self.assertEqual(page.count("data-rv-original"),1)
        self.assertLess(len(page),100000)
        self.assertIn('id="L101"',page)

    def test_page_and_filtered_neighbors_are_consistent(self):
        source=self.source()
        state=raw_view.select(source,person="A",line=2)
        self.assertEqual(state["previous"],1)
        self.assertEqual(state["next"],3)
        self.assertEqual(raw_view.select(source,page=999)["page"],1)
        self.assertEqual(raw_view.select(source,page="bad")["page"],1)

    def test_unknown_kind_value_and_invalid_json_remain_visible(self):
        raw='{"kind":"future","new_field":{"strange":[1,2]}}\n{broken\n[1,2]\n'
        self.log.write_text(raw,encoding="utf-8")
        source=self.source()
        self.assertEqual(len(source["records"]),3)
        self.assertEqual(source["records"][1].kind,"invalid")
        self.assertIn("new_field",self.render(line=1))
        self.assertIn("JSONとして整理できない",self.render(line=2))
        self.assertEqual(Original(self.render(line=2,mode="json")).text,"{broken")
        self.assertIn("値の記録",self.render(line=3))

    def test_duplicate_keys_nonfinite_values_do_not_create_false_readable_data(self):
        for raw in ('{"subject":"A","subject":"B"}','{"value":NaN}','{"value":1e9999}'):
            value,error=raw_view.parse_record(raw)
            self.assertIsNone(value)
            self.assertIsNotNone(error)

    def test_empty_and_zero_matches(self):
        page=self.render(q="nothing-here")
        self.assertIn("一致する記録がありません",page)
        self.assertNotIn("data-rv-original",page)
        self.log.write_bytes(b"")
        page=self.render()
        self.assertIn("原記録は空",page)
        self.assertNotIn("data-rv-original",page)

    def test_failed_action_does_not_claim_it_happened(self):
        record=rows()[2]
        record["effective"]=False
        self.log.write_text(json.dumps(record,ensure_ascii=False)+"\n",encoding="utf-8")
        page=self.render()
        self.assertNotIn("AがBに花束を渡した",page)
        self.assertIn("効果なし",page)

    def test_unrecorded_change_and_probabilities_are_not_zero(self):
        record=rows()[2]
        record.pop("delta")
        self.log.write_text(json.dumps(record,ensure_ascii=False)+"\n",encoding="utf-8")
        page=self.render()
        self.assertIn("変化の記録がありません",page)
        self.assertIn("確率0を意味しません",page)
        self.assertNotIn("+0.08",page)

    def test_untrusted_record_fields_and_query_are_escaped(self):
        bad='</code><script>alert(1)</script>'
        self.log.write_text(json.dumps({"kind":bad,"subject":bad,"verb":bad,"args":[bad]})+"\n",encoding="utf-8")
        page=self.render(line=1,q=bad)
        self.assertNotIn("<script>alert",page)
        self.assertIn("&lt;script&gt;",page)
        self.assertEqual(Original(page).text,self.log.read_text(encoding="utf-8").rstrip("\n"))

    def test_no_log_or_candidate_writes(self):
        before={str(p.relative_to(self.root)):(p.read_bytes(),p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file()}
        self.render(line=3,q="花束",mode="json")
        after={str(p.relative_to(self.root)):(p.read_bytes(),p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before,after)

    def test_path_containment_and_missing_file(self):
        with self.assertRaises(data.ForbiddenPath):
            raw_view.read_source(self.repo,"exp1","../escape")
        archive=self.root/"exp1"/"archive.json"
        payload=json.loads(archive.read_text())
        payload["cells"]["II|mid"]["exemplar"]["layers_path"]="../outside.jsonl"
        archive.write_text(json.dumps(payload))
        with self.assertRaises(data.ForbiddenPath):
            self.source()
        payload["cells"]["II|mid"]["exemplar"]["layers_path"]="missing.jsonl"
        archive.write_text(json.dumps(payload))
        with self.assertRaises(data.MissingResource):
            self.source()

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_clipboard_exact_text_and_denied_permission_fallback(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([shutil.which("node"),str(root/"tests/raw_workspace_ui.cjs"),
                               str(root/"viewer/static/raw-workspace.js")],
                              capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_server_passes_queries_and_serves_assets(self):
        host=server.ViewerServer(("127.0.0.1",0),server.ViewerHandler)
        host.repository=self.repo
        worker=threading.Thread(target=host.serve_forever,daemon=True)
        worker.start()
        try:
            base=f"http://127.0.0.1:{host.server_port}"
            query=urlencode({"line":3,"q":"花束","kind":"decision","person":"B","mode":"json","source":self.source()["sha256"]})
            with urlopen(base+"/exp/exp1/cell/II%7Cmid/raw?"+query) as response:
                page=response.read().decode()
            self.assertIn('id="rv-tab-json"',page)
            self.assertIn('value="花束"',page)
            self.assertIn('value="B" selected',page)
            self.assertIn('data-line="3"',page)
            for asset in ("raw-workspace.css","raw-workspace.js"):
                with urlopen(base+"/static/"+asset) as response:
                    self.assertEqual(response.status,200)
            with self.assertRaises(HTTPError) as error:
                urlopen(base+"/exp/exp1/cell/II%7Cmid/raw?line=0")
            self.assertEqual(error.exception.code,400)
        finally:
            host.shutdown()
            host.server_close()
            worker.join()

if __name__=="__main__":
    unittest.main()
