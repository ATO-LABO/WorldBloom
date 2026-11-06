"""Durable three-call interpretation, world generation and review. No automatic retry."""
from copy import deepcopy
import json
from pathlib import Path
import re
import time

from execution.provenance import ConfigError, atomic_json, canonical, contained, directory_lock, read_json, sha256
from execution.authoring_context import normalize as authoring_context, prompt as authoring_prompt
from execution.draft_goal_plan import normalize, extraction_schema, generation_schema, check_draft, atomic_review_schema, atomic_review_instructions, checked_atomic_review
from execution.draft_generation_contract import instructions, review_instructions, review_schema
from execution.draft_alignment import review_prompt, checked_review
from execution.world_drafts import _draft_request, parse_response


class PlanProvider:
    def __init__(self, settings_path):
        from execution.output_settings import resolve_generation
        selected = resolve_generation(settings_path)
        if selected['backend'] == 'none' or not selected['model'] or selected['limits']['max_calls'] < 3:
            raise ConfigError('generation', '概要からの設定作成には接続先・モデルと3回以上の呼出し上限が必要です')
        self.settings_path = settings_path
        self.max_calls = 3
        self.signature = self.current_signature()

    def current_signature(self):
        from execution.output_settings import resolve_generation, _read_settings, _section, model_credentials
        selected = resolve_generation(self.settings_path)
        return sha256(canonical({**selected, 'credentials':model_credentials(_section(_read_settings(self.settings_path), selected['backend']), selected['model'])}))

    def call(self, phase, record, elapsed):
        from execution import generation
        if self.current_signature() != self.signature:
            raise ConfigError('generation', '処理中にAI設定が変わりました。入力は保持し、自動再送しません')
        material = {k: record[k] for k in ('name', 'overview', 'authoring', 'input_settings')}
        if phase == 'extract':
            prompt = ('概要から人物・場所・品物・情報と、各人物の目的だけを抽出します。世界の下案はまだ作りません。'
                '各目的を一つの達成事項ごとに分け、概要をどう理解したかをsource_noteに短く残します。これは原文引用ではなくAIの解釈・補完で、言い換えを許容します。'
                '「場所へ向かい、他人と仲良くなりたい」はreachとbefriendの別々の目的です。'
                '複数の目的を一行へまとめたり、一部だけ残したりしてはいけません。'
                'goalsのidはg01,g02等の重複しないID。actorと全条件の参照には今回people/places/items/factsに登録した同じ名前を使います。'
                '文脈から自然に人物名や設定を補って構いません。補った内容はassumptionsに明記します。条件はreach/place, acquire/item, deliver/item+place, meet/person, '
                'befriend/person+threshold, defeat/person, survive/day, discover/factから選びます。'
                '一つの目的のconditionsは一件だけです。deliverは所持して目的地にいる状態なので、初期所持は許されます。'
                'initial_statusはunachieved/achieved/unspecified。全目的が未達成という指定は各目的へ付けます。'
                '判定できない抽象目的は文章を保持しconditionsを空にして、未対応として確認へ回します。'
                'befriendの閾値など概要にない数値・解釈はassumptionsに提案値と明記します。必要な初期設定は文脈に沿って補い、後で利用者が修正できる案として扱います。'
                '初期所持はinitial_itemsにperson/item/count/source_noteとして整理します。数量がなければ1を提案しassumptionsに明記します。'
                'JSONだけを返してください。\n' + json.dumps(material, ensure_ascii=False))
            schema = extraction_schema()
        elif phase == 'generate':
            prompt = (instructions() + '次のconfirmed_planは前段で保存したAIの目的解釈案であり、人が承認した事実ではありません。人物名・場所名・品物名・情報名、'
                'および各人物の目標の文章と条件はschemaの定数として固定されています。再解釈・省略・別目標への置換はしません。'
                'その目的が各initial_statusの状態から始まるよう、開始位置・初期所持・両方向の親しさ・経路等を設定します。'
                'unachievedのreachを持つ人はその目的地以外から始めます。unachievedのbefriendは少なくとも片方向が閾値未満です。'
                'unachievedのdeliverでも品物を初めから持っていて構いません。目的は品物の所持だけでなく目的地にいることも必要です。'
                '初期所持を勝手に取り上げたり、初期関係・名前を変えて別の目的にしたりしません。'
                '人物と場所等は計画にあるものだけを一度ずつ登録します。品物はholders、情報はname/description/place。'
                '経路はfrom/toの有向道路、逆向きにも通れるなら別行。protagonist/antagonistは異なる登録人物で、相手役は友人でも構いません。'
                '概要の初期所持、目的以外の設定も保持してください。JSONだけを返してください。\n' +
                json.dumps({**material, 'confirmed_plan': record['confirmed_plan']}, ensure_ascii=False))
            schema = generation_schema(record['confirmed_plan'])
        else:
            prompt = (review_instructions() + review_prompt(record['name'], record['overview'], record['draft']) +
                '\n確認済みの全目的と初期状態も一つずつ照合してください。\n' +
                json.dumps(record['confirmed_plan'], ensure_ascii=False))
            prompt += atomic_review_instructions()
            schema = atomic_review_schema(record['draft'])
        request = _draft_request(self.settings_path, prompt + authoring_prompt(record['authoring']),
            elapsed=elapsed, reviewing=phase != 'generate')
        request['response_schema'] = schema
        if self.current_signature() != self.signature:
            raise ConfigError('generation', '送信前にAI設定が変わりました。AIは自動再送しません')
        response = generation.transport(request, generation.preflight(request))
        return parse_response(generation.validate_response(request['backend'], response))


class GoalPlanRun:
    def __init__(self, repo, control, provider, *, editable=False, on_phase=None):
        self.repo = Path(repo)
        self.root = contained(control, 'goal-plan-runs')
        self.provider = provider
        self.started_at = time.monotonic()
        self.editable, self.on_phase = editable, on_phase
        if provider.max_calls != 3:
            raise ConfigError('generation', '3段階の上限を確認してください')

    def path(self, token):
        if not isinstance(token, str) or not re.fullmatch('[a-f0-9]{32}', token):
            raise ConfigError('request_id', '試験IDを確認してください')
        return contained(self.root, token + '.json')

    def get(self, token):
        return read_json(self.path(token))

    def save(self, record):
        atomic_json(self.path(record['request_id']), record)

    def dispatch(self, token, phase):
        with directory_lock(self.root):
            record = self.get(token)
            if len(record['calls']) >= 3 or phase in [c['phase'] for c in record['calls']]:
                raise ConfigError('generation', '実行済みの段階を自動再送しません')
            record['calls'].append({'phase': phase, 'state': 'dispatching'})
            self.save(record)  # durable reservation BEFORE transport
        start = time.monotonic()
        try:
            if self.on_phase: self.on_phase(phase, deepcopy(record))
            result = self.provider.call(phase, deepcopy(record), max(time.monotonic() - self.started_at, sum(c.get('seconds', 0) for c in record['calls'])))
        except Exception:
            with directory_lock(self.root):
                record = self.get(token)
                record['calls'][-1].update(state='uncertain_or_failed', seconds=time.monotonic() - start)
                self.save(record)
            raise
        with directory_lock(self.root):
            record = self.get(token)
            record['calls'][-1].update(state='returned', seconds=time.monotonic() - start)
            self.save(record)
        return result

    def failed(self, token, error):
        with directory_lock(self.root):
            r = self.get(token)
            r['state'] = 'invalid' if isinstance(error, ConfigError) else 'unknown'
            r['errors'] = error.field_errors if isinstance(error, ConfigError) else {'generation': '結果不明です。自動再送しません'}
            self.save(r)
        return self.get(token)

    def extract(self, token, name, overview, *, settings=None, authoring=None):
        if settings is not None:
            raise ConfigError('input_settings', 'この下案作成では個別の入力設定を指定できません')
        a = authoring_context(authoring)
        identity = {'name': name, 'overview': overview, 'input_settings': settings, 'authoring': a}
        if (not isinstance(name, str) or not name.strip() or len(name) > 120 or
                not isinstance(overview, str) or not overview.strip() or len(overview) > 8000):
            raise ConfigError('input', '名前と概要を確認してください')
        digest = sha256(canonical(identity))
        path = self.path(token)
        with directory_lock(self.root):
            if path.exists():
                old = self.get(token)
                if old['input_hash'] != digest:
                    raise ConfigError('request_id', '同じIDで入力を変えられません', code='conflict')
                return old  # pending/unknown included; NEVER replay
            self.save({'request_id': token, 'input_hash': digest, **deepcopy(identity), 'state': 'extracting', 'calls': [], 'errors': {}})
        try:
            raw = self.dispatch(token, 'extract')
            with directory_lock(self.root):
                r = self.get(token)
                r.update(raw_plan=deepcopy(raw), state='validating_plan')
                self.save(r)
            p = normalize(raw, overview)
            with directory_lock(self.root):
                r = self.get(token)
                r.update(state='awaiting_confirmation', plan=p, plan_revision=sha256(canonical(p)))
                self.save(r)
            return self.get(token)
        except Exception as error:
            return self.failed(token, error)

    def confirm(self, token, revision, plan, *, origin):
        if origin not in ('human', 'synthetic_test_oracle', 'automatic_interpretation'):
            raise ConfigError('confirmation', '確認の方法を記録してください')
        with directory_lock(self.root):
            r = self.get(token)
            recoverable = (r['state'] == 'invalid' and 'raw_plan' in r and len(r['calls']) == 1 and
                r['calls'][0]['phase'] == 'extract' and r['calls'][0]['state'] == 'returned')
            original_revision = r.get('plan_revision') or sha256(canonical(r.get('raw_plan')))
            if (r['state'] != 'awaiting_confirmation' and not recoverable) or original_revision != revision:
                raise ConfigError('revision', '目的の確認対象が変わっています', code='conflict')
            p = normalize(plan, r['overview'])
            if not self.editable and any(not g['conditions'] for g in p['goals']):
                raise ConfigError('plan', '自動判定できない目的が残っています。内容と扱いの確認が必要です')
            r.update(state='confirmed', confirmed_plan=p,
                confirmation={'origin': origin, 'plan_revision': sha256(canonical(p)), 'input_hash': r['input_hash'],
                    'extracted_revision': original_revision, 'explicitly_edited': sha256(canonical(p)) != original_revision,
                    'previous_errors': deepcopy(r['errors'])})
            r['errors'] = {}
            self.save(r)
        return self.get(token)

    def generate(self, token):
        with directory_lock(self.root):
            r = self.get(token)
            if r['state'] != 'confirmed':
                return r  # no replay after pending/failure/completion
            if (r['confirmation']['input_hash'] != r['input_hash'] or
                    r['confirmation']['plan_revision'] != sha256(canonical(r['confirmed_plan']))):
                raise ConfigError('revision', '確定した目的の内容が変わっています', code='conflict')
            r['state'] = 'generating'
            self.save(r)
        try:
            draft = self.dispatch(token, 'generate')
            if isinstance(draft, dict):
                draft.update(name=r['name'], overview=r['overview'])
                if r['input_settings'] is not None:
                    draft['settings'] = deepcopy(r['input_settings'])
            with directory_lock(self.root):
                r = self.get(token)
                r.update(draft=draft, state='validating')
                self.save(r)
            check_errors = {}
            try:
                proposal, summary, report = check_draft(self.repo, r['confirmed_plan'], draft)
            except ConfigError as error:
                if not self.editable: raise
                from execution.world_drafts import compile_draft
                proposal, _, _ = compile_draft(draft)  # only structurally safe candidates reach the editor
                check_errors = deepcopy(error.field_errors)
                summary, report = {}, {'errors':check_errors, 'needs_user_review':True}
            with directory_lock(self.root):
                r = self.get(token)
                r.update(draft=proposal, summary=summary, goal_checks=report,
                    draft_revision=sha256(canonical(proposal)), state='reviewing')
                self.save(r)
            raw_review = self.dispatch(token, 'review')
            try:
                review = checked_atomic_review(raw_review, r['confirmed_plan'], proposal)
            except ConfigError as error:
                if not self.editable: raise
                check_errors.update(error.field_errors)
                review = None
            with directory_lock(self.root):
                r = self.get(token)
                r.update(state='invalid' if check_errors else 'review', review=review,
                    review_feedback=raw_review, errors=check_errors)
                self.save(r)
            return self.get(token)
        except Exception as error:
            return self.failed(token, error)
