"""Typed UI generation boundary. No retries; legacy prompt builders stay unchanged."""
from __future__ import annotations

from dataclasses import dataclass
import json
import http.client
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import sys
import urllib.error
import urllib.request

from gapengine.synopsis import BACKENDS, _validate_response


@dataclass(frozen=True)
class Response:
    raw: bytes
    truncated: bool = False


class ContainmentError(BaseException):
    """Abort the worker if descendant termination cannot be confirmed."""


class BoundaryError(Exception):
    def __init__(self, code, cause=None):
        self.code = code
        self.cause_type = None if cause is None else type(cause).__module__ + "." + type(cause).__name__
        super().__init__(code)


def result(request, status, code, *, stage="complete", call_state="not_started",
           retry_policy="never", cause_type=None, response_ref=None, response_sha256=None):
    return {"schema_version": 1, **{k: request[k] for k in ("output_id", "candidate_id", "attempt_id")},
            "status": status, "stage": stage, "call_state": call_state, "code": code,
            "retry_policy": retry_policy, "cause_type": cause_type,
            "response_ref": response_ref, "response_sha256": response_sha256,
            "usage": None, "token_limit_supported": False,
            "message": {"ok": "本文を保存しました", "prompt_only": "プロンプトを保存しました。本文は未生成です",
                "unknown": "開始した生成処理の結果を確認できませんでした",
                "error": "生成結果を確定できませんでした", "skipped_limit": "実行上限に達したため未開始です",
                "skipped_cancelled": "停止要求により未開始です", "skipped_interrupted": "実行中断により未開始です"}[status]}


def preflight(request):
    backend, model = request["backend"], request["model"]
    if backend not in BACKENDS or not isinstance(request["prompt"], str) or not request["prompt"].strip():
        raise ValueError("invalid generation request")
    if backend != "none" and (not isinstance(model, str) or not model.strip()):
        raise ValueError("explicit model required")
    credentials = request.get("credentials") or {}
    if not isinstance(credentials, dict):
        raise ValueError("invalid credentials reference")
    if backend.endswith("-cli"):
        name = credentials.get("command", "codex" if backend == "codex-cli" else "claude")
        if not isinstance(name, str) or not name or shutil.which(name) is None:
            raise BoundaryError("launch_failed")
        args = ([shutil.which(name), "exec", "--skip-git-repo-check", "-m", model, "-"]
                if backend == "codex-cli" else [shutil.which(name), "-p", "--model", model])
        return args
    if backend != "none" and (not isinstance(credentials.get("api_key"), str) or not credentials["api_key"].strip()):
        raise ValueError("credentials missing")
    return None


def _timeout(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (TimeoutError, socket.timeout, subprocess.TimeoutExpired)):
            return True
        error = getattr(error, "reason", None) or error.__cause__ or error.__context__
        if not isinstance(error, BaseException):
            break
    return False


def transport(request, command):
    """Capture a complete bounded response, before syntax/body validation."""
    limit = request["limits"]["max_saved_response_bytes"]
    timeout = request["limits"]["call_timeout_seconds"]
    if command is not None:
        from execution.worker import ProcessTree
        try:
            tree = ProcessTree()
        except OSError as error:
            raise BoundaryError("launch_failed", error) from error
        try:
            with tempfile.TemporaryDirectory(prefix="wb-generation-cli-") as cwd:
                bridge = Path(__file__).with_name("cli_bridge.py")
                try:
                    child = tree.launch([sys.executable, "-I", "-B", str(bridge), *command], cwd, capture=True)
                except OSError as error:
                    raise BoundaryError("launch_failed", error) from error
                data, errors = bytearray(), []
                def receive():
                    try:
                        while True:
                            chunk = child.stdout.read(8192)
                            if not chunk:
                                break
                            data.extend(chunk[:max(0, limit + 1 - len(data))])
                    except OSError as error:
                        errors.append(error)
                def send():
                    try:
                        child.stdin.write(request["prompt"].encode("utf-8"))
                        child.stdin.flush()
                    except (OSError, ValueError) as error:
                        errors.append(error)
                    finally:
                        try:
                            child.stdin.close()
                        except OSError:
                            pass
                reader = threading.Thread(target=receive, daemon=True)
                writer = threading.Thread(target=send, daemon=True)
                reader.start(); writer.start()
                timeout = min(timeout, max(0.001, request.get("deadline", time.time() + timeout) - time.time()))
                try:
                    child.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    raise BoundaryError("transport_timeout", error) from error
                finally:
                    try:
                        tree.finish()
                    except OSError as error:
                        raise ContainmentError("process containment unconfirmed") from error
                    child.wait(timeout=10)
                    reader.join(timeout=10); writer.join(timeout=10)
                    child.stdout.close()
                if child.returncode in (124, 125):
                    raise BoundaryError("launch_failed")
                if child.returncode:
                    raise BoundaryError("process_exit_unknown")
                if errors or reader.is_alive() or writer.is_alive():
                    raise BoundaryError("transport_disconnected", errors[0] if errors else None)
                return Response(bytes(data[:limit]), len(data) > limit)
        finally:
            tree.close()
    backend = request["backend"]
    key = request["credentials"]["api_key"]
    if backend == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        payload = {"model": request["model"], "max_tokens": 4096,
                   "messages": [{"role": "user", "content": request["prompt"]}]}
    else:
        url = "https://api.openai.com/v1/responses"
        headers = {"Authorization": "Bearer " + key}
        payload = {"model": request["model"], "input": request["prompt"]}
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={**headers, "Content-Type": "application/json"}, method="POST")
    timeout = min(timeout, max(0.001, request.get("deadline", time.time() + timeout) - time.time()))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as stream:
            raw = stream.read(limit + 1)
    except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
        raise BoundaryError("transport_timeout" if _timeout(error) else "transport_disconnected", error) from error
    return Response(raw[:limit], len(raw) > limit)


def validate_response(backend, response):
    if response.truncated:
        raise ValueError("response exceeds storage limit")
    text = response.raw.decode("utf-8")
    if backend in ("anthropic", "openai"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("invalid response object")
        if backend == "anthropic":
            if data.get("stop_reason") not in ("end_turn", "stop_sequence"):
                raise ValueError("incomplete response")
            content = data.get("content")
            if not isinstance(content, list) or any(not isinstance(x, dict) for x in content):
                raise ValueError("invalid response content")
            parts = [x["text"] for x in content if x.get("type") == "text"]
        else:
            if data.get("status") != "completed":
                raise ValueError("incomplete response")
            output = data.get("output")
            if not isinstance(output, list) or any(not isinstance(x, dict) for x in output):
                raise ValueError("invalid response output")
            parts = []
            for item in output:
                content = item.get("content", [])
                if not isinstance(content, list) or any(not isinstance(c, dict) for c in content):
                    raise ValueError("invalid response content")
                parts.extend(c["text"] for c in content if c.get("type") == "output_text")
        if any(not isinstance(x, str) for x in parts):
            raise ValueError("invalid response text")
        text = "".join(parts)
    return _validate_response(text)


def run_generation(request, sink):
    """The sink owns durable call reservation, artifacts, receipts and projection."""
    try:
        sink.check(request)
        command = preflight(request)
    except Exception as error:
        if getattr(error, "code", None) == "conflict":
            raise
        code = error.code if isinstance(error, BoundaryError) else "preflight_failed"
        return sink.finish(result(request, "error", code, stage="preflight",
            retry_policy="safe_new_request", cause_type=type(error).__name__))
    if request["backend"] == "none":
        return sink.finish(result(request, "prompt_only", "prompt_saved"))
    # Never dispatch if reservation failed. A possibly persisted reservation is not undone.
    if not sink.reserve():
        return sink.finish(result(request, "skipped_limit", "limit_reached", retry_policy="new_budget_request"))
    try:
        response = transport(request, command)
    except Exception as error:
        code = error.code if isinstance(error, BoundaryError) else "dispatch_unknown"
        launch_failed = code == "launch_failed"
        return sink.finish(result(request, "error" if launch_failed else "unknown", code,
            stage="dispatch" if launch_failed else "receive",
            call_state="not_started" if launch_failed else "started",
            retry_policy="safe_new_request" if launch_failed else "explicit_confirmation",
            cause_type=error.cause_type if isinstance(error, BoundaryError) else type(error).__name__))
    received = None
    try:
        limit = request["limits"]["max_saved_response_bytes"]
        response = Response(response.raw[:limit], response.truncated or len(response.raw) > limit)
        received = sink.receive(response)
        text = validate_response(request["backend"], response)
    except (ValueError, KeyError, TypeError, UnicodeError, RuntimeError) as error:
        if received is None:
            raise
        return sink.finish(result(request, "error", "response_invalid", stage="validate",
            call_state="response_received", retry_policy="safe_new_request",
            cause_type=type(error).__name__, **received))
    except OSError as error:
        return sink.persistence_failure(error)
    try:
        return sink.finish(result(request, "ok", "completed", call_state="response_received", **received), text=text)
    except OSError as error:
        return sink.persistence_failure(error)


def aggregate(entries, *, stopped=None):
    statuses = [e["status"] for e in entries]
    counts = {s: statuses.count(s) for s in sorted(set(statuses))}
    if not statuses:
        raise ValueError("zero generation targets")
    if stopped is None and any(s in ("pending", "running") for s in statuses):
        raise ValueError("nonterminal generation entries")
    if stopped in ("cancelled", "interrupted"):
        state, kind = stopped, stopped
    elif all(s == "ok" for s in statuses):
        state, kind = "succeeded", "generated"
    elif all(s == "prompt_only" for s in statuses):
        state, kind = "succeeded", "prompt_only"
    elif all(s in ("ok", "prompt_only") for s in statuses):
        state, kind = "succeeded", "mixed"
    elif "ok" in statuses or "prompt_only" in statuses:
        state, kind = "partial", "partial_unknown" if "unknown" in statuses else "partial"
    elif "unknown" in statuses:
        state, kind = "failed", "unknown"
    elif all(s == "skipped_limit" for s in statuses):
        state, kind = "failed", "limit_before_start"
    elif any(s in ("pending", "running") for s in statuses):
        raise ValueError("nonterminal generation entries")
    else:
        state, kind = "failed", "error"
    return {"state": state, "completion_kind": kind, "counts": counts}
