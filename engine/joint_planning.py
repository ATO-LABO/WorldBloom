"""Shared goal-search operators for received joint offers and registered work."""
from copy import deepcopy

from engine.actions import Action
from engine.concerns import effect_coordinate


class JointPlanning:
    def __init__(self, activities):
        self.activities = activities

    def visible(self, actor):
        rt=self.activities
        values={('task',t):value for t,value in rt.known_work.get(actor.id,{}).items()}
        for tid,spec in rt.work.specs.items():
            place=(rt.calendar.contract['tasks'][tid]['place'] if rt.calendar else spec.get('place'))
            if place==actor.zone and not getattr(actor,'in_transit',False):
                values[('task',tid)]=rt.work.read(tid)['units']
        return values

    def steps(self, world, actor, root, state, turn, elapsed, spent):
        rt=self.activities
        if rt.sim is None: return
        required=world.goal_rules.affected_coordinates(world,root)
        for _ in rt.work.specs:
            for tid,task in rt.work.specs.items():
                completion=task.get('completion_effect')
                if completion and completion['kind']=='declared_state' and ('state',completion['key']) in required:
                    required.add(('task',tid))
            needed={c[1] for c in required if c[0]=='task'}
            for tid in needed & set(rt.work.specs):
                required.update(('task',dep) for dep in rt.work.specs[tid]['dependencies'])
        for inst in sorted(rt.instances.values(),key=lambda i:i['id'])[:256]:
            if inst['status'] in {'completed','canceled','interrupted','running'} or actor.id not in inst['known_by']:
                continue
            if actor.id not in rt.people(inst) or inst['consents'].get(actor.id) is False: continue
            role=next(r for r in rt.definitions[inst['definition_id']]['roles'] if actor.id in inst['roles'][r['id']])
            if any(not state.get(('knowledge',actor.id,f),False) for f in role['requires']['facts']): continue
            effects=rt.projected_effects(inst,actor,state)
            if not required & {effect_coordinate(e) for e in effects}: continue
            progress=rt.definitions[inst['definition_id']]['progress']
            if progress and any(state.get(('task',dep),-1)<rt.work.specs[dep]['units']
                                for dep in rt.work.specs[progress['task']]['dependencies']): continue
            # The search never reserves, reveals a reply, or commits progress.
            if rt.review(inst,actor)['excluded']: continue
            try:
                duration=rt._duration(inst)
                point=turn+elapsed-(rt.kernel.axis.unit=='logical_step')
                handshake=2 if rt.kernel.axis.unit=='minute' and inst['status']=='proposed' else 1
                start=inst['start'] if inst['status']=='reserved' else max(inst['earliest_start'],point+handshake)
                remaining_time=max(1,start+duration-point)
                cost=next((float(c['per_unit']['numerator']/c['per_unit']['denominator'])*duration
                           for c in rt.definitions[inst['definition_id']]['costs']
                           if actor.id in inst['roles'][c['role']]),0)
                if cost+spent>actor.stamina: continue
            except ValueError: continue
            missing=[a for a in rt.people(inst) if not (inst['consents'].get(a) and actor.id in inst['received'].get(a,[]))]
            assumptions=[{'kind':'joint_consent','actor':a,'segment':inst['id'],'expected':'accepted'}
                         for a in missing if a!=actor.id]
            if inst['status']!='reserved':
                assumptions.append({'kind':'joint_resources','segment':inst['id'],'expected':'reserved_at_start'})
            yield {'verb':'await_activity' if inst['consents'].get(actor.id) else 'join_activity',
                   'args':[inst['id'],inst['terms_hash']], 'label':rt.definitions[inst['definition_id']].get('label',inst['definition_id']),
                   'duration':remaining_time,'completion_delay':remaining_time,'cost':cost,
                   'effects':effects,'assumptions':assumptions,'meta':{'joint_segment':inst['id']}}

    def execute(self, actor, action):
        rt=self.activities
        try:
            if action.verb=='leave_activity':
                if not rt.interrupt(action.args[0],'participant_left',actor=actor.id): raise ValueError('Exit unavailable')
            elif action.verb=='await_activity':
                inst=rt.instances[action.args[0]]
                if actor.id not in inst['known_by'] or not inst['consents'].get(actor.id): raise ValueError('No accepted activity')
                return 'applied',{'joint_wait':{'id':inst['id'],'status':inst['status']}},[]
            else:
                rt.record_consent(action.args[0],actor.id,action.args[1],action.verb=='join_activity')
            return 'applied',{'joint_activity':deepcopy(rt.history[-1])},[]
        except (ValueError,KeyError,IndexError) as exc:
            return 'invalid',{'reason':str(exc)},[]
