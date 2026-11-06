"""Offline fixtures only. No gh process, GitHub request or authentication call."""
from copy import deepcopy
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from wb_delivery.contract import CODEQL_PATH, REPOSITORY, WORKFLOW_PATH, ReleaseEvidence, artifact_name, canonical
from wb_delivery.errors import DeliveryError
from wb_delivery.github_reader import GitHubReader


SHA = "a" * 40
OLDER = "b" * 40
CONFIG = {"repository_id": 1337, "workflow_id": 111, "workflow_path": WORKFLOW_PATH,
          "codeql_workflow_id": 222, "codeql_workflow_path": CODEQL_PATH}
PREFIX = "/repos/" + REPOSITORY


class FakeProcess:
    def __init__(self, payload, returncode=0):
        self.stdout = io.BytesIO(payload)
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


class Fixture:
    def __init__(self):
        self.repo = {"id": 1337, "full_name": REPOSITORY}
        self.ref = {"ref": "refs/heads/develop", "object": {"type": "commit", "sha": SHA}}
        self.workflows = {111: {"id": 111, "path": WORKFLOW_PATH, "state": "active"},
                          222: {"id": 222, "path": CODEQL_PATH, "state": "active"}}
        def run(ident, workflow_id, path):
            return {"id": ident, "run_attempt": 2, "workflow_id": workflow_id,
                    "path": path, "head_sha": SHA, "event": "push", "head_branch": "develop",
                    "repository": deepcopy(self.repo), "head_repository": deepcopy(self.repo),
                    "status": "completed", "conclusion": "success"}
        self.run = run(1001, 111, WORKFLOW_PATH)
        self.codeql = run(2001, 222, CODEQL_PATH)
        self.run_lists = {111: [self.run], 222: [self.codeql]}
        self.runs = {1001: self.run, 2001: self.codeql}
        self.jobs = [{"id": 501 + i, "name": name, "run_id": 1001, "run_attempt": 2,
                      "head_sha": SHA, "status": "completed", "conclusion": "success"}
                     for i, name in enumerate(("ci-policy", "portable"))]
        self.associations = [{"number": 31}]
        self.pulls = {31: {"number": 31, "merged": True, "merge_commit_sha": SHA,
                           "base": {"ref": "develop", "repo": deepcopy(self.repo)}}}
        self.payload = b"PK\x03\x04offline-artifact-bytes"
        self.artifact = {"id": 3001, "name": artifact_name(SHA,1001,2),
                         "expired": False, "size_in_bytes": len(self.payload),
                         "digest": "sha256:" + hashlib.sha256(self.payload).hexdigest(),
                         "workflow_run": {"id": 1001, "head_sha": SHA, "head_branch": "develop",
                                          "repository_id": 1337, "head_repository_id": 1337},
                         "archive_download_url": "https://untrusted.invalid/do-not-follow"}
        self.artifacts = [self.artifact]
        self.artifact_detail = None
        self.compare_result = {"status": "ahead", "ahead_by": 1, "behind_by": 0}
        self.commands, self.download_commands = [], []
        self.api_failure = False
        self.download_returncode = 0
        self.process = None
        self.overrides = {}

    @staticmethod
    def check_command(command, kwargs):
        assert isinstance(command, list) and command[1] == "api"
        assert command[command.index("--method") + 1] == "GET"
        assert command[command.index("--hostname") + 1] == "github.com"
        assert kwargs["shell"] is False
        assert command[-1].startswith(PREFIX)
        assert "://" not in command[-1]
        assert "auth" not in command

    @staticmethod
    def page(rows, query, key):
        assert query["per_page"] == ["100"]
        number = int(query["page"][0])
        values = rows[(number - 1) * 100:number * 100]
        return values if key is None else {"total_count": len(rows), key: values}

    def runner(self, command, **kwargs):
        self.check_command(command, kwargs)
        self.commands.append((command, kwargs))
        if self.api_failure:
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"do-not-log-token-secret")
        parsed = urlsplit(command[-1]);path = parsed.path[len(PREFIX):];query = parse_qs(parsed.query)
        if path in self.overrides:
            value = self.overrides[path]
            if callable(value):value = value(query)
        elif path == "":value = self.repo
        elif path == "/git/ref/heads/develop":value = self.ref
        elif path.startswith("/actions/workflows/"):
            ident = int(path.split("/")[3])
            if path.endswith("/runs"):
                assert query["event"] == ["push"] and query["branch"] == ["develop"]
                rows=self.run_lists[ident]
                if "head_sha" in query:rows=[row for row in rows if row.get("head_sha")==query["head_sha"][0]]
                value = self.page(rows, query, "workflow_runs")
            else:value = self.workflows[ident]
        elif path.startswith("/actions/runs/"):
            ident = int(path.split("/")[3])
            if path.endswith("/jobs"):
                assert path == f"/actions/runs/1001/attempts/{self.run['run_attempt']}/jobs"
                value = self.page(self.jobs, query, "jobs")
            elif path.endswith("/artifacts"):value = self.page(self.artifacts, query, "artifacts")
            else:value = self.runs[ident]
        elif path == "/commits/" + SHA + "/pulls":value = self.page(self.associations, query, None)
        elif path.startswith("/pulls/"):value = self.pulls[int(path.split("/")[2])]
        elif path == "/actions/artifacts/3001":value = self.artifact_detail or self.artifact
        elif path.startswith("/compare/"):value = self.compare_result
        else:raise AssertionError("Unexpected endpoint: " + path)
        raw = value if isinstance(value, bytes) else canonical(value)
        return subprocess.CompletedProcess(command, 0, stdout=raw, stderr=b"")

    def popen(self, command, **kwargs):
        self.check_command(command, kwargs)
        assert command[-1] == PREFIX + "/actions/artifacts/3001/zip"
        assert kwargs["stderr"] == subprocess.DEVNULL
        self.download_commands.append((command, kwargs))
        self.process = FakeProcess(self.payload, self.download_returncode)
        return self.process

    def reader(self, **kwargs):
        return GitHubReader(CONFIG, runner=self.runner, popen=self.popen, **kwargs)


class GitHubReaderTests(unittest.TestCase):
    def temporary_directory(self):
        project = Path(__file__).resolve().parents[1]
        parent = project / ".test-temp-github-reader"
        parent.mkdir(exist_ok=True)
        self.assertTrue(parent.resolve().is_relative_to(project.resolve()))
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.assertTrue(Path(temporary.name).resolve().is_relative_to(parent.resolve()))
        return temporary

    def test_constructor_and_import_do_not_request_or_invoke_auth(self):
        with patch("wb_delivery.github_reader.subprocess.run") as run, patch("wb_delivery.github_reader.subprocess.Popen") as popen:
            GitHubReader(CONFIG)
            run.assert_not_called();popen.assert_not_called()
        bad = [{}, {**CONFIG, "workflow_id": True}, {**CONFIG, "workflow_path": "other.yml"},
               {**CONFIG, "codeql_workflow_path": WORKFLOW_PATH}, {**CONFIG, "repository": "evil/repo"},
               {**CONFIG, "portable_job": "fake-success"}]
        for config in bad:
            with self.subTest(config=config), self.assertRaises(DeliveryError):GitHubReader(config)

    def test_existing_cli_absolute_path_is_supported_without_a_shell_or_probe(self):
        f=Fixture();config={**CONFIG,"gh_path":r"C:\Program Files\GitHub CLI\gh.exe"}
        reader=GitHubReader(config,runner=f.runner,popen=f.popen);self.assertFalse(f.commands)
        reader.resolve(SHA)
        self.assertTrue(all(c[0]==config["gh_path"] and k["shell"] is False for c,k in f.commands))
        bad_paths=["other.exe", "../gh.exe", "./gh", "C:gh.exe", r"C:\tools\cmd.exe", "gh\n--help", "gh\x00", "gh\u202e"]
        if os.name=="nt":bad_paths.append("/tools/gh")
        for value in bad_paths:
            with self.subTest(path=value),self.assertRaises(DeliveryError):GitHubReader({**CONFIG,"gh_path":value})

    def test_valid_exact_sha_evidence_and_immutable_context(self):
        f=Fixture();reader=f.reader();context=reader.resolve_context(SHA);e=context.evidence
        self.assertEqual(e,ReleaseEvidence(SHA,1001,2,111,31,3001,hashlib.sha256(f.payload).hexdigest(),len(f.payload),1337,True))
        self.assertEqual(context.merged_pr_detail["merge_commit_sha"],SHA)
        self.assertEqual(context.codeql_run_id,2001)
        with self.assertRaises(TypeError):context.merged_pr_detail["number"]=90
        f.pulls[31]["number"]=77;self.assertEqual(context.merged_pr_detail["number"],31)
        self.assertFalse(f.download_commands)
        self.assertTrue(all("/branches/" not in cmd[-1] for cmd,kwargs in f.commands))
        self.assertTrue(all("/git/ref/" not in cmd[-1] for cmd,kwargs in f.commands))

    def test_none_pins_develop_ref_to_sha_before_success_evidence_checks(self):
        f=Fixture();self.assertEqual(f.reader().resolve().source_sha,SHA)
        self.assertTrue(all("/branches/" not in c[-1] for c,k in f.commands))
        self.assertEqual(sum(c[-1]==PREFIX+"/git/ref/heads/develop" for c,k in f.commands),1)
        self.assertTrue(all("head_sha="+SHA in c[-1] for c,k in f.commands if "/workflows/" in c[-1] and "/runs?" in c[-1]))

    def test_wrong_develop_ref_type_or_sha_is_rejected(self):
        cases=[{"ref":"refs/heads/main","object":{"type":"commit","sha":SHA}},
               {"ref":"refs/heads/develop","object":{"type":"tag","sha":SHA}},
               {"ref":"refs/heads/develop","object":{"type":"commit","sha":"x"*40}},
               {"ref":"refs/heads/develop","object":{"type":"commit","sha":SHA.upper()}},
               {"ref":"refs/heads/develop","object":{"type":"commit","sha":None}},
               {"ref":"refs/heads/develop","object":None}]
        for value in cases:
            f=Fixture();f.ref=value
            with self.subTest(ref=value),self.assertRaises(DeliveryError) as error:f.reader().resolve()
            self.assertEqual(error.exception.code,"github_develop_ref")
        f=Fixture();f.ref=cases[0]
        self.assertEqual(f.reader().resolve(SHA).source_sha,SHA)
        self.assertFalse(any("/git/ref/" in c[-1] for c,k in f.commands))

    def test_ref_advancing_after_lookup_does_not_change_the_pinned_sha(self):
        f=Fixture()
        def workflow_read(query):
            f.ref["object"]["sha"]=OLDER
            return f.workflows[111]
        f.overrides["/actions/workflows/111"]=workflow_read
        self.assertEqual(f.reader().resolve().source_sha,SHA)
        self.assertEqual(f.ref["object"]["sha"],OLDER)
        self.assertEqual(sum(c[-1]==PREFIX+"/git/ref/heads/develop" for c,k in f.commands),1)
        self.assertTrue(all("head_sha="+SHA in c[-1] for c,k in f.commands if "/workflows/" in c[-1] and "/runs?" in c[-1]))

    def test_tip_without_a_run_does_not_fall_back_to_an_older_success(self):
        f=Fixture();f.ref["object"]["sha"]=OLDER
        with self.assertRaises(DeliveryError) as error:f.reader().resolve()
        self.assertEqual(error.exception.code,"github_no_run")
        self.assertFalse(f.download_commands)

    def test_tip_incomplete_or_failed_run_never_falls_back_to_older_success(self):
        for status,conclusion in [("in_progress",None),("completed","failure")]:
            f=Fixture();new=deepcopy(f.run);new.update(id=1002,head_sha=OLDER,status=status,conclusion=conclusion);f.run_lists[111].append(new);f.runs[1002]=new
            f.ref["object"]["sha"]=OLDER
            with self.subTest(status=status),self.assertRaises(DeliveryError):f.reader().resolve()

    def test_multiple_runs_for_exact_sha_are_ambiguous(self):
        f=Fixture();other=deepcopy(f.run);other["id"]=1002;f.run_lists[111].append(other)
        with self.assertRaises(DeliveryError) as e:f.reader().resolve(SHA)
        self.assertEqual(e.exception.code,"github_ambiguous_run")

    def test_run_source_repository_workflow_event_branch_and_success_are_strict(self):
        cases=[("workflow_id",999),("path","other.yml"),("event","pull_request"),("head_branch","main"),
               ("status","queued"),("conclusion","failure"),("run_attempt",True)]
        for key,value in cases:
            f=Fixture();f.run[key]=value
            with self.subTest(field=key),self.assertRaises(DeliveryError):f.reader().resolve(SHA)
        for key in ["repository","head_repository"]:
            f=Fixture();f.run[key]={"id":99,"full_name":REPOSITORY}
            with self.subTest(repo=key),self.assertRaises(DeliveryError):f.reader().resolve(SHA)

    def test_repository_and_workflow_current_identity_are_pinned(self):
        for kind in ["repo-id","repo-name","workflow-id","workflow-path","workflow-disabled"]:
            f=Fixture()
            if kind=="repo-id":f.repo["id"]=999
            elif kind=="repo-name":f.repo["full_name"]="other/repo"
            elif kind=="workflow-id":f.workflows[111]["id"]=333
            elif kind=="workflow-path":f.workflows[111]["path"]="other.yml"
            else:f.workflows[111]["state"]="disabled_manually"
            with self.subTest(kind=kind),self.assertRaises(DeliveryError):f.reader().resolve(SHA)

    def test_jobs_are_unique_successes_from_the_same_attempt_run_and_sha(self):
        for key,value in [("run_attempt",1),("run_id",123),("head_sha",OLDER),("status","in_progress"),("conclusion","skipped")]:
            f=Fixture();f.jobs[1][key]=value
            with self.subTest(field=key),self.assertRaises(DeliveryError):f.reader().resolve(SHA)
        for duplicate in [False,True]:
            f=Fixture()
            if duplicate:job=deepcopy(f.jobs[1]);job["id"]=999;f.jobs.append(job)
            else:f.jobs.pop()
            with self.subTest(duplicate=duplicate),self.assertRaises(DeliveryError):f.reader().resolve(SHA)
        f=Fixture();f.jobs[0].pop("run_attempt");f.reader().resolve(SHA)
        self.assertTrue(any("/attempts/2/jobs" in c[-1] for c,k in f.commands))

    def test_pr_detail_not_the_association_summary_is_authority(self):
        for kind in ["merged","sha","branch","repository","number"]:
            f=Fixture();p=f.pulls[31]
            if kind=="merged":p["merged"]=False
            elif kind=="sha":p["merge_commit_sha"]=OLDER
            elif kind=="branch":p["base"]["ref"]="main"
            elif kind=="repository":p["base"]["repo"]["id"]=999
            else:p["number"]=32
            with self.subTest(kind=kind),self.assertRaises(DeliveryError):f.reader().resolve(SHA)
        f=Fixture();f.associations.append({"number":32});f.pulls[32]=deepcopy(f.pulls[31]);f.pulls[32]["number"]=32
        with self.assertRaises(DeliveryError):f.reader().resolve(SHA)

    def test_codeql_failure_missing_or_pending_blocks_evidence(self):
        for kind in ["missing","failure","pending","repository","path"]:
            f=Fixture()
            if kind=="missing":f.run_lists[222]=[]
            elif kind=="failure":f.codeql["conclusion"]="failure"
            elif kind=="pending":f.codeql["status"]="in_progress"
            elif kind=="repository":f.codeql["head_repository"]["id"]=999
            else:f.codeql["path"]=WORKFLOW_PATH
            with self.subTest(kind=kind),self.assertRaises(DeliveryError):f.reader().resolve(SHA)

    def test_artifact_exact_name_expiry_digest_size_and_linked_run_are_required(self):
        for key,value in [("name","other"),("name","WorldBloom-Studio-win-x64-"+SHA),("expired",True),("expired",0),("digest",None),
                          ("digest","sha1:"+"a"*64),("size_in_bytes",0),("size_in_bytes",True),
                          ("size_in_bytes",256*1024*1024+1)]:
            f=Fixture();f.artifact[key]=value
            with self.subTest(field=key,value=value),self.assertRaises(DeliveryError):f.reader().resolve(SHA)
        for key,value in [("id",123),("head_sha",OLDER),("repository_id",999),("head_repository_id",999),("head_branch","main")]:
            f=Fixture();f.artifact["workflow_run"][key]=value
            with self.subTest(link=key),self.assertRaises(DeliveryError):f.reader().resolve(SHA)
        f=Fixture();duplicate=deepcopy(f.artifact);duplicate["id"]=3002;f.artifacts.append(duplicate)
        with self.assertRaises(DeliveryError):f.reader().resolve(SHA)

    def test_old_attempt_artifact_is_ignored_without_deleting_it(self):
        f=Fixture();old=deepcopy(f.artifact);old.update(id=2999,name=artifact_name(SHA,1001,1));f.artifacts.insert(0,old)
        self.assertEqual(f.reader().resolve(SHA).artifact_id,3001)
        self.assertEqual(len(f.artifacts),2)
        self.assertTrue(all(c[c.index("--method")+1]=="GET" for c,k in f.commands))
        f=Fixture();f.artifact["name"]=artifact_name(SHA,1001,1)
        with self.assertRaises(DeliveryError) as error:f.reader().resolve(SHA)
        self.assertEqual(error.exception.code,"github_artifact_evidence")

    def test_artifact_individual_record_is_rechecked_against_listing(self):
        f=Fixture();f.artifact_detail=deepcopy(f.artifact);f.artifact_detail["digest"]="sha256:"+"c"*64
        with self.assertRaises(DeliveryError) as e:f.reader().resolve(SHA)
        self.assertEqual(e.exception.code,"github_artifact_changed")

    def test_pagination_is_bounded_and_ambiguity_on_later_page_is_not_ignored(self):
        f=Fixture();rows=[]
        for i in range(100):
            value=deepcopy(f.run);value.update(id=5000+i,head_sha="c"*40);rows.append(value)
        rows.append(f.run);f.run_lists[111]=rows
        with self.assertRaises(DeliveryError) as e:f.reader(max_pages=1)._pages("/actions/workflows/111/runs","workflow_runs",{"branch":"develop","event":"push"})
        self.assertEqual(e.exception.code,"github_pagination_limit")
        f.commands=[];self.assertEqual(f.reader(max_pages=1).resolve().run_id,1001)
        self.assertTrue(any("head_sha="+SHA in c[-1] for c,k in f.commands))
        for row in rows:row["head_sha"]=SHA
        with self.assertRaises(DeliveryError):f.reader(max_pages=2).resolve(SHA)

    def test_duplicate_json_keys_and_api_error_bodies_are_not_accepted_or_logged(self):
        f=Fixture();f.overrides[""]=b'{"id":1337,"id":1,"full_name":"ATO-LABO/WorldBloom"}'
        with self.assertRaises(DeliveryError):f.reader().resolve(SHA)
        f=Fixture();f.api_failure=True
        with self.assertRaises(DeliveryError) as e:f.reader().resolve(SHA)
        self.assertEqual(str(e.exception),"github_read_failed");self.assertNotIn("token",str(e.exception))

    def test_compare_requires_forward_ancestry_without_divergence(self):
        f=Fixture();reader=f.reader();self.assertTrue(reader.compare(OLDER,SHA))
        for status,ahead,behind in [("behind",0,1),("diverged",2,1),("identical",0,0),("ahead",0,0),("ahead",2,1)]:
            f.compare_result={"status":status,"ahead_by":ahead,"behind_by":behind}
            self.assertFalse(reader.compare(OLDER,SHA))
        for value in [True,-1,None]:
            f.compare_result={"status":"ahead","ahead_by":1,"behind_by":value}
            with self.assertRaises(DeliveryError):reader.compare(OLDER,SHA)
        count=len(f.commands);self.assertFalse(reader.compare(SHA,SHA));self.assertEqual(len(f.commands),count)

    def test_prebuild_context_has_api_proof_without_artifact_portable_or_codeql_success(self):
        f=Fixture();f.run.update(status="in_progress",conclusion=None)
        f.jobs[1].update(status="in_progress",conclusion=None)
        f.codeql.update(status="in_progress",conclusion=None)
        f.artifact["expired"]=True
        context=f.reader().build_context(SHA,1001,2)
        self.assertEqual(context,{"repository":REPOSITORY,"event":"push","ref":"refs/heads/develop",
                                 "source_sha":SHA,"workflow_id":111,"workflow_path":WORKFLOW_PATH,
                                 "run_id":1001,"run_attempt":2,"ci_policy":"success",
                                 "merged_pr":{"number":31,"merged":True,"merge_commit_sha":SHA,
                                              "base":{"ref":"develop","repo":{"id":1337,"full_name":REPOSITORY}}}})
        self.assertFalse(any("/artifacts" in c[-1] or "/workflows/222" in c[-1] or "/runs/2001" in c[-1] for c,k in f.commands))
        self.assertFalse(f.download_commands)
        f.pulls[31]["base"]["repo"]["id"]=999
        self.assertEqual(context["merged_pr"]["base"]["repo"]["id"],1337)

    def test_prebuild_context_rejects_unmatched_attempt_identity_or_nonrunning_state(self):
        for kind in ["sha","attempt","id","branch","workflow","repository","head-repository","queued","failed"]:
            f=Fixture();sha,ident,attempt=SHA,1001,2
            if kind=="sha":sha=OLDER
            elif kind=="attempt":attempt=1
            elif kind=="id":ident=999;f.runs[999]=deepcopy(f.run)
            elif kind=="branch":f.run["head_branch"]="main"
            elif kind=="workflow":f.run["workflow_id"]=222
            elif kind=="repository":f.run["repository"]["id"]=999
            elif kind=="head-repository":f.run["head_repository"]["id"]=999
            elif kind=="queued":f.run.update(status="queued",conclusion=None)
            else:f.run["conclusion"]="failure"
            with self.subTest(kind=kind),self.assertRaises(DeliveryError):f.reader().build_context(sha,ident,attempt)

    def test_prebuild_requires_same_attempt_unique_successful_ci_policy_and_merged_pr(self):
        for kind in ["ci-failed","ci-missing","ci-duplicate","ci-attempt","not-merged","wrong-base-repo"]:
            f=Fixture()
            if kind=="ci-failed":f.jobs[0]["conclusion"]="failure"
            elif kind=="ci-missing":f.jobs.pop(0)
            elif kind=="ci-duplicate":v=deepcopy(f.jobs[0]);v["id"]=888;f.jobs.append(v)
            elif kind=="ci-attempt":f.jobs[0]["run_attempt"]=1
            elif kind=="not-merged":f.pulls[31]["merged"]=False
            else:f.pulls[31]["base"]["repo"]["id"]=999
            with self.subTest(kind=kind),self.assertRaises(DeliveryError):f.reader().build_context(SHA,1001,2)

    def test_prebuild_rechecks_run_attempt_after_pr_read(self):
        f=Fixture();calls=0
        def run_read(query):
            nonlocal calls
            calls+=1;v=deepcopy(f.run)
            if calls>1:v["run_attempt"]=3
            return v
        f.overrides["/actions/runs/1001"]=run_read
        with self.assertRaises(DeliveryError):f.reader().build_context(SHA,1001,2)

    def test_prebuild_context_cannot_replace_pc_release_evidence(self):
        f=Fixture();f.run.update(status="in_progress",conclusion=None);f.jobs[1].update(status="in_progress",conclusion=None)
        context=f.reader().build_context(SHA,1001,2)
        self.assertIsInstance(context,dict)
        with self.assertRaises(DeliveryError):f.reader().resolve(SHA)
        with self.temporary_directory() as tmp:
            with self.assertRaises(DeliveryError):f.reader().download(context,Path(tmp)/"candidate.zip")

    def test_valid_download_is_streamed_to_a_new_file_using_only_fixed_get(self):
        f=Fixture();reader=f.reader();e=reader.resolve(SHA)
        with self.temporary_directory() as tmp:
            path=Path(tmp)/"candidate.zip";self.assertEqual(reader.download(e,path),path)
            self.assertEqual(path.read_bytes(),f.payload)
        self.assertEqual(len(f.download_commands),1)
        self.assertTrue(any(c[-1].startswith(PREFIX+"/actions/artifacts/3001?") or c[-1]==PREFIX+"/actions/artifacts/3001" for c,k in f.commands))

    def test_existing_destination_is_preserved_without_new_request(self):
        f=Fixture();reader=f.reader();e=reader.resolve(SHA);f.commands=[]
        with self.temporary_directory() as tmp:
            path=Path(tmp)/"existing.zip";path.write_bytes(b"keep")
            with self.assertRaises(DeliveryError) as error:reader.download(e,path)
            self.assertEqual(error.exception.code,"download_destination_exists");self.assertEqual(path.read_bytes(),b"keep")
        self.assertFalse(f.commands);self.assertFalse(f.download_commands)

    def test_before_download_uses_new_api_metadata_not_old_evidence(self):
        f=Fixture();reader=f.reader();e=reader.resolve(SHA);f.artifact["expired"]=True
        with self.temporary_directory() as tmp:
            path=Path(tmp)/"candidate.zip"
            with self.assertRaises(DeliveryError):reader.download(e,path)
            self.assertFalse(path.exists())
        self.assertFalse(f.download_commands)

    def test_changed_run_attempt_before_download_cannot_reuse_old_evidence(self):
        f=Fixture();reader=f.reader();e=reader.resolve(SHA);f.run["run_attempt"]=3
        with self.temporary_directory() as tmp:
            with self.assertRaises(DeliveryError):reader.download(e,Path(tmp)/"candidate.zip")
        self.assertFalse(f.download_commands)

    def test_bad_download_size_digest_or_process_exit_removes_only_created_file(self):
        for kind in ["short","long","digest","exit"]:
            f=Fixture();reader=f.reader();e=reader.resolve(SHA)
            if kind=="short":f.payload=f.payload[:-1]
            elif kind=="long":f.payload+=b"x"
            elif kind=="digest":f.payload=b"x"*len(f.payload)
            else:f.download_returncode=1
            with self.temporary_directory() as tmp:
                path=Path(tmp)/"candidate.zip"
                with self.subTest(kind=kind),self.assertRaises(DeliveryError):reader.download(e,path)
                self.assertFalse(path.exists())

    def test_stream_limit_is_checked_while_reading(self):
        f=Fixture();reader=f.reader();e=reader.resolve(SHA);f.payload=b"x"*1001
        with self.temporary_directory() as tmp,patch("wb_delivery.github_reader.MAX_ARCHIVE",1000):
            path=Path(tmp)/"candidate.zip"
            with self.assertRaises(DeliveryError) as error:reader.download(e,path)
            self.assertEqual(error.exception.code,"download_archive_too_large");self.assertFalse(path.exists())

    def test_download_timeout_stops_process_and_cleans_partial_file(self):
        f=Fixture();reader=f.reader();e=reader.resolve(SHA);released=Event()
        class Blocking:
            def read(self,size):released.wait(2);return b""
        class Process(FakeProcess):
            def __init__(self):super().__init__(b"");self.stdout=Blocking()
            def kill(self):released.set();super().kill()
        process=Process()
        def start(command,**kwargs):Fixture.check_command(command,kwargs);return process
        reader=GitHubReader(CONFIG,runner=f.runner,popen=start)
        with self.temporary_directory() as tmp,patch("wb_delivery.github_reader._DOWNLOAD_TIMEOUT",.02):
            path=Path(tmp)/"candidate.zip"
            with self.assertRaises(DeliveryError) as error:reader.download(e,path)
            self.assertEqual(error.exception.code,"github_download_timeout");self.assertFalse(path.exists())
        self.assertTrue(process.killed)


if __name__=="__main__":unittest.main()
