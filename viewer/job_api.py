"""JSON routes and request boundaries for configuration and job services."""
from http import HTTPStatus
import ipaddress
from urllib.parse import urlsplit

from execution.output_settings import (
    current_generation, read_output_settings, test_generation, write_output_settings,
)
from execution.provenance import ConfigError


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


def dispatch(handler, parts, method):
    if not parts or parts[0] != "api":
        return False
    try:
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
