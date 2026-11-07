"""Configuration-only admission information for the actual draft provider.

Never probes a connection, loads a model, acquires a GPU lease or writes settings.
Reachability and model loading are deliberately not inferred from configuration.
"""
from pathlib import Path
import json


def settings_status(settings_path):
    from execution.output_settings import resolve_generation, _section
    if settings_path is None:
        return {'status': 'unconfigured'}
    try:
        raw = json.loads(Path(settings_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'status': 'unconfigured'}
    except (OSError, ValueError):
        return {'status': 'invalid_settings'}
    if not isinstance(raw, dict):
        return {'status': 'invalid_settings'}
    output = raw.get('output')
    if output is None:
        return {'status': 'unconfigured'}
    if not isinstance(output, dict):
        return {'status': 'invalid_settings'}
    from gapengine.synopsis import BACKENDS
    backend = output.get('default_backend')
    if backend is None or backend == 'none':
        return {'status': 'unconfigured'}
    if not isinstance(backend, str) or backend not in BACKENDS or not isinstance(output.get(backend, {}), dict):
        return {'status': 'invalid_settings'}
    section = _section(raw, backend)
    # Resolve exactly as existing_provider does, without current_generation(),
    # generation_availability(), preload or any network/process checks.
    selected = resolve_generation(settings_path)
    if 'model' in section and (not isinstance(section['model'], str) or not section['model'].strip()):
        return {'status': 'invalid_settings'}
    if not selected['model']:
        return {'status': 'unconfigured'}
    limits = section.get('limits', {})
    if not isinstance(limits, dict) or any(type(v) is not int or v < 1 for v in limits.values()):
        return {'status': 'invalid_settings'}
    if backend in ('openai', 'anthropic') and not str(section.get('api_key', '')).strip():
        return {'status': 'unconfigured'}
    return {'status': 'configured', 'backend': selected['backend'], 'model': selected['model'],
            'connection_checked': False, 'progress': 'combined'}


def planning_availability(store):
    """Read-only admission for the optional three-call strategy."""
    result = {'required_calls': 3, 'progress': 'phases'}
    provider = store.provider
    if provider is None or store.mode == 'waiting':
        return {**result, 'status': 'unconfigured'}
    if not callable(getattr(provider, 'plan_factory', None)):
        return {**result, 'status': 'unsupported_provider'}
    status = getattr(provider, 'planning_status', None)
    if not callable(status):
        return {**result, 'status': 'configuration_unknown'}
    try:
        result.update(status())
        if result.get('status') == 'configured':
            budget = result.get('max_calls')
            if type(budget) is not int or budget < 3:
                result['status'] = 'insufficient_budget'
            elif store._plan_gate.locked():
                result['status'] = 'busy'
        return result
    except Exception:
        return {**result, 'status': 'invalid_settings'}


def availability(store):
    result = _legacy_availability(store)
    return {**result, 'strategies': {'goal-plan-v1': planning_availability(store)}}


def _legacy_availability(store):
    if store.provider is None or store.mode == 'waiting':
        return {'status': 'unconfigured', 'settings_route': '/configs?tab=output'}
    if store.mode == 'mock':
        return {'status': 'configured', 'mock': True, 'connection_checked': False, 'progress': 'combined'}
    provider = store.provider
    if any(not callable(getattr(provider, name, None)) for name in ('with_context', 'review_with_context', 'review')):
        return {'status': 'unsupported_provider', 'settings_route': None}
    status = getattr(provider, 'configuration_status', None)
    if not callable(status):
        return {'status': 'configuration_unknown', 'settings_route': None}
    try:
        result = status()
        if result.get('status') == 'configured' and callable(getattr(provider, 'busy', None)) and provider.busy():
            return {'status': 'busy', 'settings_route': None}
        return {**result, 'settings_route': '/configs?tab=output' if result.get('status') in ('unconfigured', 'invalid_settings') else None}
    except Exception:
        return {'status': 'invalid_settings', 'settings_route': None}
