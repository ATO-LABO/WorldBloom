"""JSON routes and request boundaries for configuration and job services."""
from http import HTTPStatus
import ipaddress
from urllib.parse import parse_qs, urlsplit

from execution.evolution_settings import read_evolution_settings, write_evolution_settings
from execution.output_settings import (
    current_generation, list_models, read_output_settings, test_generation,
    write_api_key, write_output_settings,
)
from execution.provenance import ConfigError
from gapengine.gpu_guard import GpuBusy


STATUS = {"not_found":404, "conflict":409, "unavailable":503,
          "forbidden":403, "bad_request":400}


def loopback(name):
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def boundary(handler, *, client_header=True, body_required=True):
    def deny():
        raise ConfigError("request", "許可されていないリクエストです", code="forbidden")
    if not loopback(handler.client_address[0]):
        deny()
    if len(handler.headers.get_all("Host", [])) != 1 or len(handler.headers.get_all("Origin", [])) > 1:
        deny()
    host = handler.headers.get("Host", "")
    try:
        parsed = urlsplit("http://" + host)
        port = parsed.port or 80
        if (not parsed.hostname or not loopback(parsed.hostname) or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or port != handler.server.server_port):
            deny()
        origin = handler.headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            deny()
    except ValueError:
        deny()
    if handler.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
        deny()
    if client_header and handler.headers.get("X-WorldBloom-Client") != "1":
        deny()
    if not body_required:
        return
    if handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ConfigError("request", "JSON形式で送信してください", code="bad_request")
    if handler.headers.get("Transfer-Encoding") or len(handler.headers.get_all("Content-Length", [])) != 1:
        raise ConfigError("request", "本文長が不正です", code="bad_request")


def _label(availability):
    # deferred: workbench_pages imports this module at load time, so the
    # reverse import must happen at call time, not module top level.
    from viewer.workbench_pages import availability_label
    return availability_label(availability)


def _labeled_availability(settings):
    availability = current_generation(settings)["availability"]
    return {**availability, "label": _label(availability)}


def send_error(handler, error):
    body = error.as_dict()
    body.setdefault("current_revision", None)
    handler._send_json(HTTPStatus(STATUS.get(error.code, 422)), body)


def send_data_error(handler, error):
    from viewer import data
    if isinstance(error, data.ForbiddenPath):
        code, message = "forbidden", "この場所は表示できません"
    elif isinstance(error, data.MissingResource):
        code, message = "not_found", "対象の情報が見つかりません"
    else:
        code, message = "bad_request", str(error)
    send_error(handler, ConfigError("request", message, code=code))


def dispatch(handler, parts, method):
    if not parts or parts[0] != "api":
        return False
    try:
        if method == "GET" and parts == ["api", "status", "local"]:
            # Unlike every other /api route, this must work without a
            # job_store: the Viewer exe (no job_store at all) still shows the
            # topbar's GPU/AI status dialog, just with everything as "off".
            boundary(handler, client_header=False, body_required=False)
            from viewer import local_status
            settings = getattr(handler.server, "settings_path", None)
            handler._send_json(HTTPStatus.OK, local_status.snapshot(settings))
            return True
        if method == "POST" and parts in (["api", "status", "local", "preload"], ["api", "status", "local", "unload"]):
            # Same job_store exemption as the GET route above -- the Viewer
            # exe never shows these buttons (backend resolves to None there),
            # but the route itself doesn't need job_store to run.
            handler.connection.settimeout(5)
            try:
                body = handler._request_json()
            except ValueError as error:
                raise ConfigError("request", "JSON本文が不正です", code="bad_request") from error
            boundary(handler)
            from viewer import local_status
            settings = getattr(handler.server, "settings_path", None)
            is_preload = parts[3] == "preload"
            if is_preload:
                if body:
                    raise ConfigError("request", "読み込み要求の本文は空オブジェクトにしてください")
            else:
                # Unlike preload (always the currently-configured backend),
                # unload names an explicit backend: a config change after
                # preloading must not strand the release button with
                # nothing to target (WB-PRELOAD-001 review M1).
                backend = body.get("backend")
                if set(body) != {"backend"} or backend not in ("llama-server", "ollama"):
                    raise ConfigError(
                        "request", 'backendに"llama-server"か"ollama"を指定してください', code="bad_request",
                    )
            try:
                if is_preload:
                    handler._send_json(HTTPStatus.ACCEPTED, local_status.start_preload(settings))
                else:
                    handler._send_json(HTTPStatus.OK, local_status.stop_preload(settings, backend))
            except GpuBusy as error:
                raise ConfigError(
                    "preload", f"GPUが使用中です: {error.holder.get('owner', 'unknown')}", code="conflict",
                ) from error
            except ValueError as error:
                raise ConfigError("preload", str(error)) from error
            return True
        jobs = getattr(handler.server, "job_store", None)
        if jobs is None:
            raise ConfigError("service", "実行管理は未設定です", code="unavailable")
        configs = jobs.configs
        settings = getattr(handler.server, "settings_path", None)
        if method == "GET":
            boundary(handler, client_header=False, body_required=False)
        if method == "POST":
            handler.connection.settimeout(5)
            try:
                body = handler._request_json()
            except ValueError as error:
                raise ConfigError("request", "JSON本文が不正です", code="bad_request") from error
            boundary(handler)
        if method == "GET" and parts == ["api", "configs"]:
            handler._send_json(HTTPStatus.OK, {"configs":configs.list()})
        elif method == "GET" and len(parts) == 3 and parts[1] == "configs":
            handler._send_json(HTTPStatus.OK, configs.get(parts[2]))
        elif method == "POST" and parts == ["api", "configs", "preview"]:
            handler._send_json(HTTPStatus.OK, configs.preview(body))
        elif method == "POST" and parts == ["api", "configs"]:
            handler._send_json(HTTPStatus.CREATED, configs.save(body))
        elif method == "GET" and parts == ["api", "settings", "output"]:
            view = read_output_settings(settings)
            handler._send_json(HTTPStatus.OK, {**view, "availability": _labeled_availability(settings)})
        elif method == "POST" and parts == ["api", "settings", "output", "api-key"]:
            write_api_key(settings, body.get("backend"), body.get("api_key"))
            view = read_output_settings(settings)
            handler._send_json(HTTPStatus.OK, {"backends": view["backends"]})
        elif method == "GET" and parts == ["api", "settings", "output", "models"]:
            from viewer.workbench_pages import reason_label
            backend = (parse_qs(urlsplit(handler.path).query).get("backend") or [None])[0]
            catalog = list_models(settings, backend)
            handler._send_json(HTTPStatus.OK, {**catalog, "reason_label": reason_label(catalog["reason"])})
        elif method == "POST" and parts == ["api", "settings", "output"]:
            write_output_settings(settings, body)
            view = read_output_settings(settings)
            handler._send_json(HTTPStatus.OK, {**view, "availability": _labeled_availability(settings)})
        elif method == "POST" and parts == ["api", "settings", "output", "test"]:
            availability = test_generation(settings, body.get("backend"), body.get("model"))
            view = read_output_settings(settings)
            handler._send_json(HTTPStatus.OK, {
                "availability": {**availability, "label": _label(availability)},
                "backends": view["backends"],
            })
        elif method == "GET" and parts == ["api", "settings", "evolution"]:
            handler._send_json(HTTPStatus.OK, read_evolution_settings(settings))
        elif method == "POST" and parts == ["api", "settings", "evolution"]:
            handler._send_json(HTTPStatus.OK, write_evolution_settings(settings, body))
        elif method == "GET" and parts == ["api", "outputs"]:
            handler._send_json(HTTPStatus.OK, {"outputs": jobs.outputs()})
        elif method == "GET" and len(parts) == 3 and parts[1] == "outputs":
            handler._send_json(HTTPStatus.OK, jobs.output(parts[2]))
        elif method == "POST" and len(parts) == 4 and parts[1] == "outputs" and parts[3] == "recover":
            if body:
                raise ConfigError("request", "復旧要求の本文は空オブジェクトにしてください")
            handler._send_json(HTTPStatus.OK, jobs.output(parts[2], recover=True))
        elif method == "GET" and parts == ["api", "jobs"]:
            handler._send_json(HTTPStatus.OK, {"jobs":jobs.list()})
        elif method == "GET" and len(parts) == 3 and parts[1] == "jobs":
            handler._send_json(HTTPStatus.OK, jobs.get(parts[2]))
        elif method == "POST" and parts == ["api", "jobs"]:
            job, created = jobs.submit(body, settings_path=settings)
            handler._send_json(HTTPStatus.ACCEPTED if created else HTTPStatus.OK, job)
        elif method == "POST" and len(parts) == 4 and parts[1] == "jobs" and parts[3] == "cancel":
            if body:
                raise ConfigError("request", "停止要求の本文は空オブジェクトにしてください")
            handler._send_json(HTTPStatus.ACCEPTED, jobs.cancel(parts[2]))
        else:
            raise ConfigError("route", "APIがありません", code="not_found")
    except ConfigError as error:
        send_error(handler, error)
    except FileNotFoundError:
        send_error(handler, ConfigError("resource", "指定された設定または記録がありません", code="not_found"))
    except (OSError, ValueError, TypeError, KeyError):
        handler._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
            {"code":"storage_error", "message":"保存済み記録を処理できません", "field_errors":{},
             "retryable":False, "current_revision":None})
    return True
