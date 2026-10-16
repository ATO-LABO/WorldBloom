"""Independent trial routes under the existing local JSON/CSRF boundary."""
from http import HTTPStatus
from urllib.parse import urlsplit,parse_qs
from execution.provenance import ConfigError,identifier
from execution.independent_review import capability
from viewer import job_api

def dispatch(handler,parts,method):
    if len(parts)<2 or parts[:2]!=['api','independent']:return False
    try:
        jobs=getattr(handler.server,'job_store',None)
        if jobs is None:raise ConfigError('service','実行管理は未設定です',code='unavailable')
        if method=='GET':job_api.boundary(handler,client_header=False,body_required=False)
        else:
            handler.connection.settimeout(5)
            body=handler._request_json();job_api.boundary(handler)
        if method=='GET' and parts==['api','independent','capabilities']:
            query=parse_qs(urlsplit(handler.path).query)
            cid=query.get('config_id',[None])[0]
            if cid is None:
                caps=[capability(jobs.configs,c['config_id']) for c in jobs.configs.list()]
                handler._send_json(HTTPStatus.OK,{'schema_version':1,'configurations':caps})
            else:handler._send_json(HTTPStatus.OK,capability(jobs.configs,identifier(cid,'config_id')))
        elif method=='POST' and parts==['api','independent','jobs']:
            if not isinstance(body,dict) or body.get('kind')!='independent_trials':
                raise ConfigError('kind','独立試行の要求だけを受け付けます',code='bad_request')
            value,created=jobs.submit(body)
            handler._send_json(HTTPStatus.ACCEPTED if created else HTTPStatus.OK,value)
        elif method=='GET' and len(parts)==4 and parts[2]=='jobs':
            value=jobs.get(parts[3])
            if value.get('kind')!='independent_trials':raise ConfigError('kind','独立試行のジョブではありません',code='bad_request')
            handler._send_json(HTTPStatus.OK,value)
        elif method=='POST' and len(parts)==5 and parts[2]=='jobs' and parts[4]=='cancel':
            if body:raise ConfigError('request','取消の本文は空にしてください',code='bad_request')
            if jobs.get(parts[3]).get('kind')!='independent_trials':raise ConfigError('kind','独立試行のジョブではありません',code='bad_request')
            handler._send_json(HTTPStatus.OK,jobs.cancel(parts[3]))
        elif method=='GET' and len(parts)==5 and parts[2]=='jobs' and parts[4]=='result':
            handler._send_json(HTTPStatus.OK,jobs.independent_result(parts[3]))
        else:raise ConfigError('route','対象がありません',code='not_found')
    except ConfigError as error:
        if error.code=='unsupported':handler._send_json(HTTPStatus.NOT_IMPLEMENTED,error.as_dict())
        else:job_api.send_error(handler,error)
    except (ValueError,TypeError,KeyError) as error:
        job_api.send_error(handler,ConfigError('request','要求または保存結果の形式を確認してください',code='bad_request'))
    return True
