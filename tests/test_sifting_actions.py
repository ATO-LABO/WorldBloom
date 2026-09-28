"""Recorded actions, fixed-candidate fidelity and read-only HTTP boundaries."""
from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from unittest.mock import patch
from execution.provenance import ConfigError, atomic_json
from viewer import action_log, raw_view
from viewer.data import RunRepository
from viewer.server import ViewerServer, ViewerHandler


def payload(actor="A"):
    rows = [{"kind":"header","protagonist":actor},
            {"kind":"event","subject":actor,"verb":"encounters"},
            {"kind":"decision","subject":actor,"verb":"give_item","day":4,"slot":"夕方", "turn":19,
             "args":["B","<img onerror=alert(1)>"],"details":{"target":"B","item":"花束"},
             "effective":True,"policy":{"route":{"text":"<script>危険</script>"}},
             "delta":{"relations":[{"observer":actor,"target":"B","affinity":0.25}]},
             "choice_prob":0.42},
            {"kind":"snapshot","day":4},
            {"kind":"decision","subject":"B","verb":"move","args":["森"],"effective":False},
            {"kind":"decision","verb":"rest"}]
    return ("\n".join(json.dumps(r,ensure_ascii=False) for r in rows)+"\nnot json\n").encode()


class ActionLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="wb-sifting-actions-");self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.runs=self.root/"runs";self.exp=self.runs/"old";self.exp.mkdir(parents=True)
        (self.exp/"a.jsonl").write_bytes(payload());(self.exp/"b.jsonl").write_bytes(payload("C"))
        atomic_json(self.exp/"archive.json",{"cells":{key:{"generation":g,"quality":0.5,"reach_rate":1,
            "exemplar":{"seed":g,"layers_path":path},"parents":[],"genome":{}}
            for key,g,path in (("I|low",0,"a.jsonl"),("VI|high",1,"b.jsonl"))}})
        self.repo=RunRepository(self.runs,control_root=self.root/"control")
        self.catalog=self.repo.catalog;self.rid=self.catalog.register_legacy("old")
        self.snap=self.catalog.snapshot(self.rid);self.c=self.snap["candidates"]["candidates"][0]
        self.query={"publication":[str(self.snap["revision"])],"source":[self.c["source_log_sha256"]]}

    def load(self,**query):
        return action_log.load(self.catalog,self.rid,self.c["candidate_id"],{**self.query,**query})

    def test_physical_lines_and_actual_times_without_guesses(self):
        value=self.load();self.assertEqual([r["line"] for r in value["rows"]],[3,5,6])
        self.assertEqual(value["rows"][0]["day"],"4");self.assertEqual(value["rows"][0]["slot"],"夕方")
        self.assertEqual(value["rows"][1]["day"],"");self.assertEqual(value["rows"][2]["result"],"未記録")
        self.assertEqual(value["actors"],["","A","B"]);self.assertEqual(value["protagonist"],"A")
        self.assertEqual(value["invalid_count"],1)

    def test_selected_detail_is_escaped_and_bound_to_exact_line(self):
        value=self.load(line=["3"])
        self.assertIn("42.00%",value["html"]);self.assertIn("+0.25",value["html"])
        self.assertNotIn("<script>",value["html"]);self.assertIn("&lt;script&gt;",value["html"])
        self.assertIn("line=3",value["raw_href"]);self.assertIn(self.c["source_log_sha256"],value["raw_href"])
        self.assertIn("未記録",self.load(line=["5"])["html"])

    def test_other_candidate_does_not_resolve_representative_cell(self):
        candidates=self.snap["candidates"]["candidates"]
        c=deepcopy(candidates[1]);c["candidate_id"]="cand-nonrepresentative";c["role"]="antagonist";c["cell_key"]=candidates[0]["cell_key"]
        snapshot=deepcopy(self.snap);snapshot["candidates"]["candidates"].append(c)
        with patch.object(self.catalog,"snapshot",return_value=snapshot):
            result=action_log.load(self.catalog,self.rid,c["candidate_id"],{"publication":[str(self.snap["revision"])],"source":[c["source_log_sha256"]]})
        self.assertEqual(result["protagonist"],"C");self.assertEqual(result["source"],c["source_log_sha256"])

    def test_publication_hash_and_missing_source_fail_closed(self):
        for query in ({"publication":["999"]},{"source":["wrong"]}):
            with self.assertRaises(ConfigError) as error:self.load(**query)
            self.assertEqual(error.exception.code,"conflict")
        (self.exp/self.c["log"]["relative_path"]).write_bytes(payload("changed"))
        with self.assertRaises(ConfigError):self.load()

    def test_invalid_lines_and_unknown_parameters(self):
        for query in ({"line":["0"]},{"line":["2"]},{"line":["7"]},{"line":["1.5"]},{"line":["3","5"]},{"path":["../b.jsonl"]}):
            with self.subTest(query=query),self.assertRaises(ConfigError):self.load(**query)

    def test_empty_source_and_unicode_physical_boundaries(self):
        source=raw_view.source_bytes(b'{"kind":"header"}\n',relative="x",experiment="x",cell="x")
        self.assertEqual(action_log.overview(source)["rows"],[])
        source=raw_view.source_bytes('{"kind":"decision","verb":"rest","subject":"A\u2028B"}\r\n'.encode(),relative="x",experiment="x",cell="x")
        self.assertEqual(action_log.overview(source)["rows"][0]["line"],1)

    def test_changed_state_values_are_not_claimed_to_be_increments(self):
        html=action_log.state_changes({"subject":"A","delta":{"actor":{"inventory":{"木材":3},"stamina":4}}})
        self.assertIn("変更後の値",html);self.assertIn("所持品",html);self.assertNotIn("+3",html)

    def test_http_endpoint_and_page_keep_original_bytes_and_controls(self):
        before=(self.exp/"a.jsonl").read_bytes()
        server=ViewerServer(("127.0.0.1",0),ViewerHandler);server.repository=self.repo
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=10)
            url=f'/api/runs/{self.rid}/candidates/{self.c["candidate_id"]}/actions?'+urlencode({k:v[0] for k,v in self.query.items()})
            connection.request("GET",url);response=connection.getresponse();self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read())["rows"][0]["line"],3)
            connection.request("GET",url,headers={"Sec-Fetch-Site":"cross-site"});response=connection.getresponse();self.assertEqual(response.status,403);response.read()
            connection.request("GET",f'/runs/{self.rid}/candidates');response=connection.getresponse();page=response.read().decode();self.assertEqual(response.status,200)
            self.assertIn('data-sf-tab="actions"',page);self.assertIn('data-sf-action-note',page);self.assertIn('name="sf-verdict"',page)
            connection.close()
        finally:server.shutdown();thread.join();server.server_close()
        self.assertEqual((self.exp/"a.jsonl").read_bytes(),before)


if __name__=="__main__":unittest.main()
