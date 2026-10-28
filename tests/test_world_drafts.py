"""Offline contract, no real model, job, network provider or original data."""
from copy import deepcopy
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from execution.configs import ConfigStore
from execution.jobs import JobStore
from execution.provenance import ConfigError
from execution.world_drafts import DraftStore, compile_draft, existing_provider, mock_response, parse_response, validate
from viewer.data import RunRepository
from viewer.server import ViewerHandler, ViewerServer, build_parser

ROOT = Path(__file__).resolve().parents[1]


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-draft-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        shutil.copytree(ROOT / "templates/basic", self.repo / "templates/basic")
        self.control = self.root / "control"
        self.calls = 0
        def provider(name, overview):
            self.calls += 1
            return mock_response(name, overview)
        self.store = DraftStore(self.repo, self.control, provider, "mock")
        self.token = uuid4().hex

    def create(self):
        return self.store.create(self.token, "灯の町", "森から光を取り戻す物語")

    def test_generate_check_adopt_is_new_only_and_idempotent(self):
        r = self.create()
        self.assertEqual(r["state"], "review")
        self.assertEqual(r["mode"], "mock")
        self.assertFalse((self.repo / "projects").exists())
        self.assertEqual(self.create(), r)
        self.assertEqual(self.calls, 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: self.store.adopt(self.token, r["revision"]), range(2)))
        self.assertEqual(a, b)
        self.assertEqual(a["state"], "adopted")
        self.assertEqual(len([p for p in (self.repo / "projects").iterdir() if p.is_dir()]), 1)
        saved = self.repo / "projects" / a["world_id"]
        self.assertTrue((saved / "world.yaml").is_file())
        self.assertEqual(len(list((saved / "subjects").glob('*.yaml'))), 2)

    def test_existing_world_never_overwritten(self):
        r = self.create()
        saved = self.repo / "projects" / r["world_id"]
        saved.mkdir(parents=True)
        (saved / "sentinel.txt").write_text("keep")
        with self.assertRaises(ConfigError):
            self.store.adopt(self.token, r["revision"])
        self.assertEqual((saved / "sentinel.txt").read_text(), "keep")
        self.assertFalse((saved / "world.yaml").exists())

    def test_input_change_with_same_id_is_rejected(self):
        self.create()
        with self.assertRaises(ConfigError):
            self.store.create(self.token, "別世界", "違う概要")
        self.assertEqual(self.calls, 1)

    def test_cancel_during_generation_discards_late_response(self):
        started, release = threading.Event(), threading.Event()
        def slow(name, overview):
            started.set()
            self.assertTrue(release.wait(5))
            return mock_response(name, overview)
        self.store.provider = slow
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.create)
            self.assertTrue(started.wait(5))
            self.assertEqual(self.store.cancel(self.token)["state"], "cancelled")
            release.set()
            self.assertEqual(future.result()["state"], "cancelled")
        with self.assertRaises(ConfigError):
            self.store.adopt(self.token, "stale")
        self.assertFalse((self.repo / "projects").exists())

    def test_duplicate_during_generation_does_not_call_provider_again(self):
        started, release = threading.Event(), threading.Event()
        def slow(name, overview):
            self.calls += 1
            started.set(); release.wait(5)
            return mock_response(name, overview)
        self.store.provider = slow
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.create)
            self.assertTrue(started.wait(5))
            self.assertEqual(self.create()["state"], "generating")
            release.set(); future.result()
        self.assertEqual(self.calls, 1)

    def test_unknown_generation_is_durable_and_not_retried(self):
        def failed(*_):
            self.calls += 1
            raise TimeoutError("not echoed")
        self.store.provider = failed
        self.assertEqual(self.create()["state"], "unknown")
        self.assertEqual(self.create()["state"], "unknown")
        self.assertEqual(self.calls, 1)
        self.assertFalse((self.repo / "projects").exists())

    def test_invalid_response_and_new_explicit_retry(self):
        self.store.provider = lambda *_: '{"name": "broken"}'
        self.assertEqual(self.create()["state"], "invalid")
        self.store.cancel(self.token)
        self.token = uuid4().hex
        self.store.provider = mock_response
        self.assertEqual(self.create()["state"], "review")

    def test_reference_errors_keep_draft_and_no_world(self):
        r = self.create(); changed = deepcopy(r["draft"])
        changed["people"][0]["entry"] = "未登録の港"
        with self.assertRaises(ConfigError) as caught:
            self.store.update(self.token, r["revision"], changed)
        self.assertIn("people.0.entry", caught.exception.field_errors)
        self.assertEqual(self.store.get(self.token), r)
        self.assertFalse((self.repo / "projects").exists())

    def test_valid_revision_change_blocks_old_adoption(self):
        r = self.create(); changed = deepcopy(r["draft"])
        changed["people"][0]["description"] = "人が確認した紹介"
        updated = self.store.update(self.token, r["revision"], changed)
        with self.assertRaises(ConfigError):
            self.store.adopt(self.token, r["revision"])
        self.assertEqual(self.store.adopt(self.token, updated["revision"])["state"], "adopted")

    def test_changed_template_after_validation_requires_new_review(self):
        r = self.create()
        path = self.repo / "templates/basic/rules.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        with self.assertRaises(ConfigError):
            self.store.adopt(self.token, r["revision"])
        self.assertFalse((self.repo / "projects").exists())

    def test_save_outcome_unknown_does_not_publish_twice(self):
        r = self.create()
        from execution.world_drafts import atomic_json as actual_write
        def interrupted(path, value):
            if value.get("state") == "adopted":
                raise OSError("simulated interruption after publication")
            return actual_write(path, value)
        with patch("execution.world_drafts.atomic_json", side_effect=interrupted):
            with self.assertRaises(OSError): self.store.adopt(self.token, r["revision"])
        self.assertEqual(self.store.get(self.token)["state"], "adopting")
        with self.assertRaises(ConfigError): self.store.adopt(self.token, r["revision"])
        self.assertEqual(len([p for p in (self.repo / "projects").iterdir() if p.is_dir()]), 1)

    def test_validation_never_runs_simulation_or_provider(self):
        with patch("engine.sim.Simulation.run", side_effect=AssertionError("no simulation")), \
             patch("execution.generation.transport", side_effect=AssertionError("no provider")):
            result = validate(self.repo, parse_response(mock_response("x", "y")))
        self.assertEqual(result[3]["subjects"], 2)

    def test_structure_refs_unknown_keys_numbers_and_paths(self):
        base = parse_response(mock_response("x", "y"))
        mutations = [lambda d: d.update(scheduled_events=[]),
                     lambda d: d["places"].append(deepcopy(d["places"][0])),
                     lambda d: d["people"][0]["goal"].update(target="unknown"),
                     lambda d: d["people"][0]["relations"][0].update(person=[]),
                     lambda d: d["people"][0]["relations"][0].update(affinity=True),
                     lambda d: d["people"][0]["relations"][0].update(awareness=2),
                     lambda d: d["routes"][0].update(to="unknown")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                d = deepcopy(base); mutate(d)
                with self.assertRaises(ConfigError): compile_draft(d)
        for token in ("../escape", "", "x" * 32):
            with self.assertRaises(ConfigError): self.store.get(token)
        for text in ('{"a":1,"a":2}', '{"a":NaN}', "not json", "x" * 65000):
            with self.assertRaises(ConfigError): parse_response(text)

    def test_default_gate_prevents_any_provider_call(self):
        waiting = DraftStore(self.repo, self.control)
        with patch("execution.generation.transport", side_effect=AssertionError("no live calls")):
            with self.assertRaises(ConfigError): waiting.create(self.token, "x", "y")
        self.assertFalse(waiting.root.exists())

    def test_optional_route_conditions_compile_without_changing_legacy_routes(self):
        d = parse_response(mock_response('x', 'y'))
        _, original, _ = compile_draft(d)
        self.assertEqual(original['routes']['町'], [{'to':'森'}])
        d['routes'][0].update(cost=2.5, requires_item='灯りの種')
        compiled, world, _ = compile_draft(d)
        self.assertEqual(world['routes']['町'], [{'to':'森','cost':2.5,'requires_item':'灯りの種'}])
        self.assertEqual(world['routes']['森'], [{'to':'町'}])
        self.assertEqual(compiled['routes'][0]['cost'], 2.5)

    def test_invalid_route_conditions_and_directed_pairs_are_rejected(self):
        base = parse_response(mock_response('x', 'y'))
        for cost in [0, -1, 10001, True, '2', float('inf'), float('nan')]:
            with self.subTest(cost=cost):
                d = deepcopy(base);d['routes'][0]['cost'] = cost
                with self.assertRaises(ConfigError):compile_draft(d)
        for item in ['', '未登録', None, []]:
            with self.subTest(item=item):
                d=deepcopy(base);d['routes'][0]['requires_item']=item
                with self.assertRaises(ConfigError):compile_draft(d)
        for route in [{'from':'町','to':'町'}, {'from':'町','to':'森','unexpected':1}]:
            d=deepcopy(base);d['routes'][0]=route
            with self.assertRaises(ConfigError):compile_draft(d)
        d=deepcopy(base);d['routes'].append(deepcopy(d['routes'][0]))
        with self.assertRaises(ConfigError):compile_draft(d)

    def test_item_and_route_edits_persist_in_normal_world_model(self):
        from execution.world_editor import snapshot
        from execution.library import LibraryStore
        r=self.create();d=deepcopy(r['draft'])
        d['items']=[{'name':'地図','holder':'レン'},{'name':'鍵','holder':'アオ'}]
        d['people'][0]['goal']['target']='地図'
        d['routes'][0].update(cost=2.5,requires_item='鍵')
        updated=self.store.update(self.token,r['revision'],d)
        with self.assertRaises(ConfigError):self.store.adopt(self.token,r['revision'])
        adopted=self.store.adopt(self.token,updated['revision'])
        model=snapshot(LibraryStore(self.repo),adopted['world_id'])
        people={p['id']:p for p in model['people']}
        self.assertEqual(people['アオ']['inventory'],{'鍵':1})
        self.assertEqual(people['レン']['inventory'],{'地図':1})
        self.assertEqual(people['アオ']['goal']['target'],'地図')
        self.assertEqual(model['world']['routes']['町'],[{'to':'森','cost':2.5,'requires_item':'鍵'}])
        self.assertEqual(model['world']['routes']['森'],[{'to':'町'}])

    def test_existing_adapter_only_uses_mocked_transport(self):
        settings = self.root / "settings.json"
        settings.write_text(json.dumps({"output": {"default_backend": "ollama", "ollama": {"model": "test-model"}}}), encoding="utf-8")
        from execution.generation import Response
        raw = json.dumps({"message": {"content": mock_response("x", "y")}, "done": True, "done_reason": "stop"}).encode()
        with patch("execution.generation.transport", return_value=Response(raw)) as transport:
            value = existing_provider(settings)("x", "y")
        self.assertEqual(parse_response(value)["name"], "x")
        self.assertEqual(transport.call_count, 1)
        request = transport.call_args.args[0]
        self.assertEqual(request["model"], "test-model")
        self.assertEqual(request["limits"]["max_saved_response_bytes"], 64000)
        self.assertEqual(request["limits"]["call_timeout_seconds"], 300)
        self.assertEqual(request["response_format"], "world_draft_json")

    def test_ollama_world_draft_requests_json_without_changing_story_contract(self):
        from execution.generation import transport
        request = {"backend":"ollama", "model":"test-model", "prompt":"test",
                   "credentials":{"base_url":"http://127.0.0.1:11434", "think":False},
                   "limits":{"max_saved_response_bytes":64000,"call_timeout_seconds":300}}
        for kind in ("world_draft_json", "story_json", None):
            with self.subTest(kind=kind):
                with patch("urllib.request.urlopen", return_value=BytesIO(b'{}')) as send:
                    transport({**request,"response_format":kind}, None)
                payload = json.loads(send.call_args.args[0].data)
                self.assertEqual(payload["model"], "test-model")
                self.assertFalse(payload["think"])
                self.assertFalse(payload["stream"])
                if kind == "world_draft_json": self.assertEqual(payload["format"], "json")
                elif kind == "story_json": self.assertEqual(payload["format"]["required"], ["paragraphs"])
                else: self.assertNotIn("format", payload)


class DraftHTTPTests(unittest.TestCase):
    def setUp(self):
        DraftTests.setUp(self)
        (self.root / "runs").mkdir()
        self.server = ViewerServer(('127.0.0.1', 0), ViewerHandler)
        self.server.job_store = JobStore(ConfigStore(self.repo, self.control, self.root / "runs"))
        self.server.job_store.world_draft_mode = "mock"
        self.server.world_drafts = self.store
        self.server.repository = RunRepository(self.root / "runs")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)

    def request(self, path, body=None, client=True):
        host, port = self.server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=15)
        self.addCleanup(connection.close)
        headers = {'Content-Type': 'application/json', 'Origin': f'http://{host}:{port}'}
        if client: headers['X-WorldBloom-Client'] = '1'
        connection.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body), headers)
        response = connection.getresponse(); raw = response.read()
        return response.status, raw

    def test_http_review_then_explicit_adopt(self):
        status, raw = self.request('/worlds/new?method=ai')
        self.assertEqual(status, 200)
        self.assertIn('モック応答'.encode(), raw)
        self.assertIn(b'world-draft.js', raw)
        for asset, content_type in (("world-draft.js", b"application/javascript"), ("world-draft.css", b"text/css")):
            asset_status, asset_raw = self.request('/static/'+asset)
            self.assertEqual(asset_status, 200, asset)
            self.assertTrue(asset_raw, asset)
        self.assertEqual(self.calls, 0)
        status, raw = self.request('/api/world-drafts', {'request_id': self.token, 'name': 'HTTPの世界', 'overview': '設定の下案'})
        self.assertEqual(status, 200, raw)
        result = json.loads(raw);self.assertEqual(result['state'], 'review')
        self.assertFalse((self.repo/'projects').exists())
        status, raw = self.request('/api/world-drafts/'+self.token+'/adopt', {'revision':result['revision']})
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)['state'], 'adopted')
        self.assertEqual(self.calls, 1)

    def test_post_boundary_rejects_missing_client_and_extra_fields(self):
        status, _ = self.request('/api/world-drafts', {'request_id':self.token,'name':'x','overview':'y'}, client=False)
        self.assertEqual(status, 403)
        status, _ = self.request('/api/world-drafts', {'request_id':self.token,'name':'x','overview':'y','mode':'live'})
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, 0)

    def test_non_delivery_world_is_validated_and_adopted_through_http(self):
        proposal = parse_response(mock_response("x", "y"))
        proposal["items"] = []
        proposal["people"][0]["goal"] = {"description":"森の秘密を明かす", "conditions":[{"type":"discover", "fact":"鐘の秘密"}]}
        proposal["facts"] = [{"name":"鐘の秘密", "description":"水車が鐘を動かしている", "place":"森"}]
        self.store.provider = lambda *_: json.dumps(proposal)
        status, raw = self.request('/api/world-drafts', {'request_id':self.token, 'name':'鐘の町', 'overview':'調査員が町の秘密を調べる'})
        self.assertEqual(status, 200)
        record = json.loads(raw)
        self.assertEqual(record["state"], "review", record)
        status, raw = self.request('/api/world-drafts/'+self.token+'/adopt', {'revision':record["revision"]})
        self.assertEqual(status, 200, raw)
        path = self.repo/'projects'/record['world_id']
        self.assertIn("鐘の秘密", (path/'world.yaml').read_text(encoding='utf8'))


if __name__ == '__main__': unittest.main()
