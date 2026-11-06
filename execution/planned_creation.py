"""Bridge durable interpretation runs to the existing editable draft/adoption contract."""
from copy import deepcopy
from execution.provenance import ConfigError, atomic_json, directory_lock
from execution.draft_goal_plan import annotations
from execution.goal_plan_pipeline import GoalPlanRun


def run(store, token, name, overview, settings, authoring):
    from execution.world_drafts import validate, validated_revision, CHECK_VERSION
    factory = getattr(store.provider, 'plan_factory', None)
    if not callable(factory):
        raise ConfigError('generation', 'この接続では3段階の設定作成を利用できません。手入力で続けられます')
    def progress(phase, record):
        with directory_lock(store.root):
            current = store.get(token)
            if current['state'] != 'generating':
                raise ConfigError('generation', 'この下案の作成は取消・変更済みです。次のAI処理は開始しません')
            current.update(phase=phase, model_calls=len(record['calls']))
            if record.get('confirmed_plan') is not None:
                current['goal_plan'] = deepcopy(record['confirmed_plan'])
                current['goal_plan_notes'] = annotations(current['goal_plan'], overview)
            atomic_json(store._path(token), current)
    runner = GoalPlanRun(store.repo, store.root.parent, factory(), editable=True, on_phase=progress)
    extracted = runner.extract(token, name, overview, settings=settings, authoring=authoring)
    if extracted['state'] != 'awaiting_confirmation':
        return {'state':extracted['state'],'draft':extracted.get('draft'),
                'errors':extracted['errors'],'failure_reason':'generation_rejected',
                'phase':'extract','model_calls':len(extracted['calls'])}
    # This records an AI interpretation, never a fabricated user approval.
    runner.confirm(token, extracted['plan_revision'], extracted['plan'], origin='automatic_interpretation')
    result = runner.generate(token)
    notes = {'goal_plan':deepcopy(result['confirmed_plan']),
             'goal_plan_notes':annotations(result['confirmed_plan'],overview),
             'model_calls':len(result['calls']), 'phase':'complete'}
    if result['state'] != 'review':
        safe_candidate = isinstance(result.get('draft'),dict) and isinstance(result.get('draft_revision'),str)
        return {**notes,'state':result['state'],'draft':result.get('draft') if safe_candidate else None,
                'errors':result['errors'],'review_feedback':result.get('review_feedback'),
                'failure_reason':'review_failed' if 'review_feedback' in result else 'invalid_draft',
                'editable_generated_candidate':safe_candidate}
    proposal, world, subjects, summary = validate(store.repo,result['draft'])
    revision = validated_revision(store.repo,proposal,world,subjects)
    return {**notes,'state':'review','draft':proposal,'summary':summary,'revision':revision,
            'alignment':{'revision':revision,'check_version':CHECK_VERSION,'review':result['review']},'errors':{}}
