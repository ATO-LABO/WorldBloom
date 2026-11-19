"""Durable refinement proposals. Adoption writes one draft snapshot, never a world.

This backend is deliberately not wired to viewer routes yet. A planner adapter must
be explicitly supplied; reads, rejection and adoption never invoke a model.
"""
from copy import deepcopy
from execution.provenance import atomic_json, directory_lock, read_json, ConfigError, contained
from execution.refinement_contract import VERSION, digest, fail, capabilities, compile_plan, validate_candidate

class Refinements:
    def __init__(self, store, planner=None):
        self.store = store
        self.planner = planner

    def path(self, token, request_id):
        self.store._path(token); self.store._path(request_id)
        return contained(self.store.root, 'refinements/' + token + '/' + request_id + '.json')

    def base_unchanged(self, current, request):
        try: self.store._editable(current, request['base_revision'])
        except ConfigError: return False
        return (digest(current.get('draft')) == request['base_hash'] and
                digest(current.get('goal_plan')) == request['goal_hash'] and
                digest(current.get('authoring')) == request['authoring_hash'])

    def get(self, token, request_id):
        try: return read_json(self.path(token, request_id))
        except FileNotFoundError: fail('request_id', 'Proposal not found', 'not_found')

    def preview(self, token, revision, request_id, *, mode, prompt='', preset=None, targets=None):
        path = self.path(token, request_id)
        if mode not in ('prompt', 'preset') or not isinstance(prompt, str) or len(prompt) > 4000:
            fail('request', 'Invalid input mode or prompt')
        if mode == 'prompt' and (not prompt.strip() or preset is not None): fail('prompt', 'Prompt required without preset')
        if mode == 'preset' and preset not in capabilities()['presets']: fail('preset', 'Unsupported preset', 'unsupported')
        targets = [] if targets is None else targets
        if not isinstance(targets, list) or any(not isinstance(x, str) for x in targets) or len(set(targets)) != len(targets):
            fail('targets', 'Expected unique entity names')
        inputs = {'revision': revision, 'mode': mode, 'prompt': prompt, 'preset': preset, 'targets': targets}
        with directory_lock(self.store.root):
            if path.exists():
                old = read_json(path)
                if old['input_hash'] != digest(inputs): fail('request_id', 'Request id reused with different input', 'conflict')
                return old
            record = self.store.get(token)
            self.store._editable(record, revision)
            if self.planner is None: fail('generation', 'Refinement planner is not connected', 'unavailable')
            r = {'request_id': request_id, 'input_hash': digest(inputs), 'inputs': inputs,
                 'base_revision': revision, 'base_hash': digest(record['draft']),
                 'goal_hash': digest(record.get('goal_plan')), 'authoring_hash': digest(record.get('authoring')),
                 'contract_version': VERSION, 'state': 'generating', 'model_calls_reserved': 1,
                 'requires_review':record['mode'] != 'mock' or callable(getattr(self.planner,'review',None)),
                 'candidate': None, 'changes': [], 'errors': {}}
            atomic_json(path, r)  # Reserve before dispatch. Never retry a persisted request.
        try:
            decomposition = getattr(self.planner, 'decompose', None)
            requirements = decomposition(deepcopy(record), deepcopy(inputs), capabilities()) if mode == 'prompt' and callable(decomposition) else None
            if requirements is not None:
                with directory_lock(self.store.root):
                    saved = read_json(path)
                    if saved['state'] == 'cancelled': return saved
                    if not self.base_unchanged(self.store.get(token), r): fail('revision', 'Draft changed', 'conflict')
                    saved.update(requirements=deepcopy(requirements), model_calls_reserved=2)
                    atomic_json(path, saved)
                    r.update(requirements=deepcopy(requirements), model_calls_reserved=2)
            plan = self.planner(deepcopy(record), deepcopy(inputs), capabilities())
            with directory_lock(self.store.root):
                saved = read_json(path)
                if saved['state'] == 'cancelled': return saved
                saved['raw_plan'] = deepcopy(plan)
                atomic_json(path, saved)
                r['raw_plan'] = deepcopy(plan)
            candidate, changes = compile_plan(record['draft'], plan, preset=preset, targets=targets)
            proposal, world, subjects, summary = validate_candidate(self.store, record, candidate)
            from execution.world_drafts import validated_revision
            next_revision = validated_revision(self.store.repo, proposal, world, subjects)
            # Existing semantic review remains a distinct prerequisite for live drafts.
            # Until a bounded model adapter supplies its receipt, do not mark live proposals ready.
            alignment = None
            if r['requires_review']:
                reviewer = getattr(self.planner, 'review', None)
                if not callable(reviewer): fail('alignment', 'Live semantic review adapter is not connected', 'unavailable')
                with directory_lock(self.store.root):
                    saved = read_json(path)
                    if saved['state'] == 'cancelled': return saved
                    self.store._editable(self.store.get(token), revision)
                    saved.update(state='validating', model_calls_reserved=r['model_calls_reserved']+1)
                    atomic_json(path, saved)
                    r.update(state='validating', model_calls_reserved=saved['model_calls_reserved'])
                from execution.draft_alignment import checked_review, CHECK_VERSION
                review = checked_review(reviewer(deepcopy(record), deepcopy(inputs), deepcopy(proposal)), proposal)
                alignment = {'revision':next_revision, 'check_version':CHECK_VERSION, 'review':review}

            result = {'state': 'ready', 'candidate': proposal, 'candidate_id': request_id,
                      'candidate_hash': digest(proposal), 'changes': changes, 'summary': summary,
                      'candidate_revision': next_revision, 'plan': plan, 'alignment':alignment,
                      'model_provenance':getattr(self.planner, 'provenance', {}),
                      'warnings': ['simulation_not_tested'] + ([] if record.get('goal_plan') else ['legacy_goal_plan_unavailable'])}
        except ConfigError as error:
            result = {'state': error.code if error.code in ('unsupported', 'needs_clarification', 'unavailable') else 'invalid', 'errors': error.field_errors}
        except Exception:
            result = {'state': 'unknown', 'errors': {'generation': 'Planner result unknown; no automatic retry'}}
        result['generation_feedback'] = deepcopy(getattr(self.planner, 'raw_generation', None))
        result['requirement_feedback'] = deepcopy(getattr(self.planner, 'raw_requirements', None))
        result['review_feedback'] = deepcopy(getattr(self.planner, 'raw_review', None))
        raw_plan = r.get('raw_plan')
        if isinstance(raw_plan, dict):
            result['clarification'] = deepcopy(raw_plan.get('clarification', []))
            result['unsupported'] = deepcopy(raw_plan.get('unsupported', []))
        with directory_lock(self.store.root):
            saved = read_json(path)
            if saved['state'] == 'cancelled': return saved
            current = self.store.get(token)
            try: self.store._editable(current, revision)
            except ConfigError: result = {'state': 'stale', 'errors': {'revision': 'Draft changed'}}
            if digest(current.get('draft')) != r['base_hash']: result = {'state': 'stale', 'errors': {'revision': 'Draft changed'}}
            r.update(result); atomic_json(path, r)
        return r

    def save_input(self, token, revision, draft):
        """Persist the human working copy with zero inference; old review is invalidated."""
        from execution.world_drafts import validate, validated_revision
        with directory_lock(self.store.root):
            current = self.store.get(token)
            self.store._editable(current, revision)
            if not isinstance(draft, dict) or any(draft.get(k) != current[k] for k in ('name','overview')):
                fail('draft', 'Original name and overview must be preserved')
            proposal, world, subjects, summary = validate(self.store.repo, draft)
            if current.get('goal_plan'):
                from execution.refinement_contract import check_refined_goals
                check_refined_goals(self.store.repo, current['goal_plan'], proposal)
            updated_revision = validated_revision(self.store.repo, proposal, world, subjects)
            if proposal == current['draft'] and updated_revision == current['revision']: return current
            current.update(draft=proposal, revision=updated_revision, summary=summary,
                           alignment=None, state='review', errors={})
            current.pop('edit_revision', None)
            current.pop('last_refinement', None)
            atomic_json(self.store._path(token), current)
            return self.store.get(token)

    def cancel(self, token, request_id):
        with directory_lock(self.store.root):
            r = self.get(token, request_id)
            if r['state'] == 'applying':
                current = self.store.get(token)
                if self.base_unchanged(current, r):
                    r['state'] = 'ready'  # No commit occurred; cancellation is safe under the same lock.
            if r['state'] in ('applied', 'applying'): fail('state', 'Cannot cancel an adoption', 'conflict')
            r['state'] = 'cancelled'; atomic_json(self.path(token, request_id), r)
            return r

    def apply(self, token, request_id, revision, candidate_id, candidate_hash, *, acknowledged_warnings=None):
        path = self.path(token, request_id)
        with directory_lock(self.store.root):
            r = self.get(token, request_id); current = self.store.get(token)
            if candidate_id != r.get('candidate_id') or candidate_hash != r.get('candidate_hash'):
                fail('candidate', 'Candidate identity mismatch', 'conflict')
            # Draft commit marker recovers a crash after draft write but before receipt write.
            marker = current.get('last_refinement', {})
            if r['state'] in ('applied', 'applying') and marker == {'request_id': request_id, 'candidate_hash': candidate_hash, 'revision': current['revision']}:
                r.update(state='applied', applied_revision=current['revision']); atomic_json(path, r)
                return current
            if r['state'] == 'applying' and self.base_unchanged(current, r):
                r['state'] = 'ready'  # Pre-commit interruption: revalidate and commit, without inference.
            if r['state'] != 'ready' or r['contract_version'] != VERSION:
                fail('state', 'Proposal is not ready', 'conflict')
            self.store._editable(current, revision)
            if revision != r['base_revision'] or digest(current['draft']) != r['base_hash'] or digest(current.get('goal_plan')) != r['goal_hash'] or digest(current.get('authoring')) != r['authoring_hash']:
                fail('revision', 'Draft or protected context changed; request a new proposal', 'conflict')
            if not isinstance(acknowledged_warnings, list) or set(acknowledged_warnings) != set(r['warnings']):
                fail('warnings', 'Acknowledge the displayed warnings')
            if digest(r['candidate']) != candidate_hash: fail('candidate', 'Saved candidate modified', 'conflict')
            proposal, world, subjects, summary = validate_candidate(self.store, current, r['candidate'])
            from execution.world_drafts import validated_revision
            updated_revision = validated_revision(self.store.repo, proposal, world, subjects)
            if updated_revision != r['candidate_revision']: fail('revision', 'Validation inputs changed', 'conflict')
            if r.get('requires_review', True):
                from execution.draft_alignment import checked_review, CHECK_VERSION
                receipt = r.get('alignment') or {}
                if receipt.get('revision') != updated_revision or receipt.get('check_version') != CHECK_VERSION:
                    fail('alignment', 'Candidate review receipt is missing or stale', 'conflict')
                checked_review(receipt.get('review'), proposal)
            r['state'] = 'applying'; atomic_json(path, r)
            current.update(draft=proposal, revision=updated_revision, summary=summary, state='review', errors={}, alignment=r.get('alignment'))
            current.pop('edit_revision', None); current.pop('needs_revalidation', None)
            current['last_refinement'] = {'request_id': request_id, 'candidate_hash': candidate_hash, 'revision': updated_revision}
            atomic_json(self.store._path(token), current)
            r.update(state='applied', applied_revision=updated_revision); atomic_json(path, r)
            return current
