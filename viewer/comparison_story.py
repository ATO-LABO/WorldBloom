"""Source-bound, deterministic reading aids; never generates prose on a GET."""
import hashlib

from execution.provenance import contained
from gapengine import reader_summary
from gapengine.scenes import RESULT_LABELS
from gapengine.world_patch_inputs import verify_frozen
from viewer import explanation_ui as ex


def frozen_goal(experiment):
    """Only this run's sealed inputs may supply intended ending conditions."""
    if not (experiment / 'manifest.json').exists():
        return None
    config = verify_frozen(experiment)
    import yaml
    world_path = contained(experiment, f"inputs/projects/{config['project_id']}/world.yaml")
    raw = world_path.read_bytes()
    world = yaml.safe_load(raw)
    ids = config['preview'].get('target_endings', [])
    endings = {e['id']: e for e in world.get('ending', [])}
    labels = [endings[i].get('label') or i for i in ids if i in endings]
    if not labels or len(labels) != len(ids):
        return None
    return {'labels': labels, 'ids': ids, 'world_sha256': hashlib.sha256(raw).hexdigest(),
            'config_sha256': hashlib.sha256((experiment / 'config.json').read_bytes()).hexdigest(),
            'path': world_path.relative_to(experiment).as_posix()}


def action_text(value):
    if value.get('verb') == 'share_knowledge' and len(value.get('args', [])) > 1 and value['args'][1] == '雑談':
        return f"{value['subject']}が{value['args'][0]}と雑談する"
    return ex.action_text(value)


def describe(explanation):
    """Facts retain citations and distinguish intended goal from observed ending."""
    empty = {'title': None, 'goal': None, 'choice': None, 'consequence': None,
             'ending': None, 'lines': [], 'label': '原記録から整理', 'goal_source': None}
    if not explanation:
        return empty
    goal = explanation.get('comparison_goal')
    if goal:
        empty.update(goal=' ／ '.join(goal['labels']), goal_source=goal['path'])
    if not reader_summary.is_summarizable(explanation):
        return empty
    try:
        packet = reader_summary.build_packet(explanation)
    except (OSError, ValueError, KeyError, TypeError):
        return empty
    facts = packet['facts']
    action = next(f for f in facts if f['kind'] == 'executed_action')
    choice = action_text(action['value'])
    later = [f for f in facts if f['kind'] == 'executed_later_action_not_total_causal_proof']
    endings = [f for f in facts if f['kind'] == 'later_ending_not_total_causal_proof']
    # Use executed descriptions only; never use a pending effect's promised text.
    def later_text(fact):
        value = fact['value']
        text = value.get('executed_description') or action_text(value)
        result = value.get('result')
        if not value.get('executed_description') and result:
            text += '（結果: ' + {**RESULT_LABELS, 'paid_off': '伏線回収', 'misjudged': '見立て違い'}.get(result, str(result)) + '）'
        return text
    consequence = ' ／ '.join(later_text(f) for f in later)
    ending = ' ／ '.join(f['value']['label'] for f in endings) or None
    if ending:
        consequence += (' ／ ' if consequence else '') + '到達した結末: ' + ending
    if not consequence:
        outcome = next((f['value'].get('result') for f in facts if f['kind'] == 'executed_result'), None)
        consequence = ('直後の結果: ' + RESULT_LABELS.get(outcome, str(outcome)) + '。' if outcome else '') + '後続のつながり・結末は未確認'
    payoff = next((f['value']['executed_description'] for f in later if f['value'].get('executed_description')), None)
    title = (choice + ' — ' + payoff) if payoff else choice + (' — ' + ending if ending else '')
    lines = sorted({n for f in [action, *later, *endings] for n in f['lines']})
    result = dict(empty, title=title, choice=choice, consequence=consequence, ending=ending, lines=lines)
    reader = explanation.get('reader_summary')
    if reader:
        summary = reader['summary']
        result.update(title=summary['title']['text'], label='AI要約・照合済み' if reader.get('reviewed') else 'AI要約・未照合')
        if summary.get('choice'):
            result['choice'] = summary['choice']['text']
        if summary.get('turning'):
            result['consequence'] = summary['turning']['text']
        if summary.get('goal') and goal:
            result['goal'] = summary['goal']['text']
    return result
