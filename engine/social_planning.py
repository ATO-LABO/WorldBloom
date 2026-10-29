"""Actor-scoped conditional protocol operators for the common goal search.

Forecast replies never execute a peer action. Existing deliberation and actual
typed physical operators remain the authority for every real result.
"""
from copy import deepcopy
import json

from engine.actions import Action
from engine.concerns import coordinates, effect_coordinate, shape, distinct, identifier
from engine.deliberation import VERBS as PROPOSAL_VERBS
from engine.action_timing import step_timing


VERBS = PROPOSAL_VERBS | {'await_response','request_response','answer_response','share_knowledge','negotiate','concede','give_item'}


def key(pid, field): return ('state', '@protocol:'+json.dumps([pid,field],ensure_ascii=False))
def effect(coord, value): return {'kind':'state','key':coord[1],'value':value}


class SocialPlanning:
    def __init__(self, raw):
        shape(raw, {'version','proposals','requests'})
        if type(raw['version']) is not int or raw['version'] != 1: raise ValueError('Unsupported social planning version')
        if not isinstance(raw['proposals'],list) or not isinstance(raw['requests'],list) or len(raw['proposals'])>8 or len(raw['requests'])>32:
            raise ValueError('Bounded social operators required')
        self.config=deepcopy(raw); self.proposals={}; self.requests={}
        for spec in raw['proposals']:
            shape(spec,{'proposal','requires','reopen'})
            distinct(spec['requires'])
            if not isinstance(spec['proposal'],str) or spec['proposal'] in self.proposals or type(spec['reopen']) is not bool:
                raise ValueError('Invalid social proposal binding')
            self.proposals[spec['proposal']]=deepcopy(spec)
        for spec in raw['requests']:
            shape(spec,{'id','requester','responder','action'})
            for value in spec.values(): identifier(value)
            if spec['requester']==spec['responder']: raise ValueError('Cooperation needs distinct participants')
            if spec['id'] in self.requests: raise ValueError('Duplicate cooperation binding')
            self.requests[spec['id']]=deepcopy(spec)
        if set(self.requests)&set(self.proposals): raise ValueError('Proposal and request IDs must be distinct')
        self.reset()

    def reset(self):
        self.views={}; self.messages={}; self.history=[]; self.links={}

    def bind(self, world):
        if world.planner is None or world.concerns is None: raise ValueError('Social planning requires goals and conditions')
        for pid,spec in self.proposals.items():
            if world.deliberation is None or pid not in world.deliberation.specs: raise ValueError('Unknown social proposal')
            if not set(spec['requires']) <= set(world.concerns.conditions): raise ValueError('Unknown social prerequisite')
        for spec in self.requests.values():
            if world.slot_execution is not None: raise ValueError('Native cooperation requires a native task binding; proactive response requests use ordinary execution')
            if not {spec['requester'],spec['responder']} <= set(world.subjects): raise ValueError('Unknown cooperation actor')
            if world.responses is None or spec['action'] not in world.responses.actions:
                raise ValueError('Cooperation requires a declared proactive operator')
            action=world.responses.actions[spec['action']]
            if action['signal'] is not None or spec['responder'] not in action['actors']: raise ValueError('Invalid proactive cooperation operator')
        self.reset()

    def known(self, world, actor, pid):
        found=self.views.get(actor,{}).get(pid)
        if found is not None: return found
        spec=world.deliberation.specs[pid]
        if spec['proposer']==actor:
            return {'status':'draft','revision':0,'alternative':None,'terms_hash':None,'known_by':{},'reviews':{},'versions':[]}
        return None

    def visible(self, world, actor):
        result={}
        for pid in self.proposals:
            record=self.known(world,actor.id,pid)
            if record is None: continue
            spec=world.deliberation.specs[pid]
            phase=record['status']
            if phase=='draft' and actor.id==spec['proposer']:
                result.update({('state',k):world.deliberation.config['state'][k] for k in spec['requires_state']})
            if phase=='open' and set(record['reviews'])==set(spec['reviewers']): phase='reviewed'
            result[key(pid,'phase')]=phase; result[key(pid,'revision')]=record['revision']
            result[key(pid,'alternative')]=record['alternative'] or ''
            for who in spec['reviewers']: result[key(pid,'heard:'+who)]=record['known_by'].get(who)==record['revision']
        for rid,spec in self.requests.items():
            if actor.id!=spec['requester']: continue
            message=self.messages.get(rid)
            result[key(rid,'request')]=message.get('known_reply') or 'asked' if message else 'unasked'
        for event in self.history:
            if event.get('kind')=='disclosure' and actor.id in event['audience']:
                result[('knowledge',event['to'],event['fact'])]=True
        return result

    def expand(self, world, actor, needed):
        """Only public operator structure and the requester's own requirements."""
        for pid,binding in self.proposals.items():
            spec=world.deliberation.specs[pid]
            if spec['proposer']!=actor.id: continue
            if any({('state',k) for k in effects}&needed for effects in spec['outcomes'].values()):
                for cid in binding['requires']: needed.update(coordinates(world.concerns.conditions[cid]['test']))
                needed.update(('knowledge',actor.id,f) for f in spec['profiles'][actor.id]['requires'])
                needed.update(('state',k) for k in spec['requires_state'])
        for request in self.requests.values():
            if request['requester']!=actor.id: continue
            spec=world.responses.actions[request['action']]
            if needed.intersection(map(effect_coordinate,spec['effects'])):
                needed.update(('state',k) for k in spec['preconditions'])
                auth=spec.get('authorization')
                if auth:
                    needed.update(('state',k) for k in world.deliberation.specs[auth['proposal']]['outcomes'][auth['status']])

    def wait_duration(self, world, args):
        if len(args)!=4 or args[0] not in {'proposal','request'}: raise ValueError('Invalid reply wait')
        kind,ident,phase,revision=args
        if kind=='proposal':
            if ident not in self.proposals or phase not in {'reviews','decision'} or not revision.isdecimal(): raise ValueError('Unknown proposal wait')
            return 1
        if ident not in self.requests or phase!='completion': raise ValueError('Unknown cooperation wait')
        work=world.responses.actions[self.requests[ident]['action']]['steps'];reply=1
        if world.action_timing is not None:
            rules=world.action_timing.rules
            if type(rules.get('respond',{}).get('duration')) is int: work=rules['respond']['duration']
            if type(rules.get('answer_response',{}).get('duration')) is int: reply=rules['answer_response']['duration']
        return work+reply

    def _step(self, world, actor, verb, args, label, effects, turn, elapsed, assumptions=()):
        raw=self.wait_duration(world,args) if verb=='await_response' else 1
        duration,cost,completion=step_timing(world,actor,verb,tuple(args),raw,0,turn=turn,elapsed=elapsed)
        if world.slot_execution is not None: completion=duration
        return {'verb':verb,'args':list(args),'label':label,'duration':duration,'completion_delay':completion,
                'cost':cost,'effects':effects,'assumptions':list(assumptions),'meta':{}}

    def steps(self, world, actor, root, state, turn, elapsed, spent):
        from engine.phase2 import available_verbs
        calendar=(world.slot_execution or {}).get('mode')=='calendar_promises_v2'
        day=(turn+elapsed)//1440+1 if calendar else (turn+elapsed-1)//len(world.slots)+1
        allowed=available_verbs(world,actor,turn=turn+elapsed,day=day)
        needed=set(world.goal_rules.affected_coordinates(world,root))
        if world.responses is not None:
            _,needed=world.planner._relevant(world,actor,root)
        self.expand(world,actor,needed)
        def offer(verb,args,label,effects,assumptions=()):
            if verb in allowed and world.genre_allows(verb):
                return [self._step(world,actor,verb,args,label,effects,turn,elapsed,assumptions)]
            return []
        for pid,binding in self.proposals.items():
            spec=world.deliberation.specs[pid]
            if spec['proposer']!=actor.id or state.get(('zone',actor.id))!=spec['place']: continue
            relevant=any({('state',k) for k in v}&needed for v in spec['outcomes'].values())
            if not relevant: continue
            projection={'effects':world.planner.effects(state)}
            if any(world.concerns.evaluate(world,cid,actor=actor.id,projection=projection)['state']!='true' for cid in binding['requires']): continue
            if any(not state.get(('knowledge',actor.id,f),False) for f in spec['profiles'][actor.id]['requires']): continue
            phase=state.get(key(pid,'phase'),'draft'); revision=int(state.get(key(pid,'revision'),0)); rev=str(revision)
            known=self.known(world,actor.id,pid)
            if phase in {'draft','rejected','deferred'}:
                if phase=='draft' and any(state.get(('state',k))!=v for k,v in spec['requires_state'].items()): continue
                if phase!='draft' and not binding['reopen']: continue
                used={v['alternative'] for v in known['versions']}
                for alt in spec['alternatives']:
                    if phase=='draft' and alt['id']!=spec['initial'] or alt['id'] in used: continue
                    if world.deliberation._assessment(world,spec,actor.id,alt['terms'],projection=projection)['stance']!='support': continue
                    effects=[effect(key(pid,'phase'),'open'),effect(key(pid,'revision'),revision+1),effect(key(pid,'alternative'),alt['id'])]
                    for who in spec['reviewers']:
                        effects.append(effect(key(pid,'heard:'+who),state.get(('zone',who))==spec['place']))
                    yield from offer('submit_proposal' if phase=='draft' else 'amend_proposal',[pid,alt['id'],rev],spec['title']+'を提出・修正',effects)
            elif phase=='open':
                missing=[who for who in spec['reviewers'] if not state.get(key(pid,'heard:'+who),False)]
                if missing:
                    for who in missing:
                        if state.get(('zone',who))==spec['place']:
                            yield from offer('present_proposal',[pid,who,rev],who+'に提案を説明',[effect(key(pid,'heard:'+who),True)])
                elif actor.id in spec['reviewers'] and actor.id not in known['reviews'] and revision==known['revision']:
                    yield from offer('review_proposal',[pid,rev],'自分の立場で提案を審査',[effect(key(pid,'phase'),'reviewed')],
                                     [{'external_reply':pid,'revision':revision,'stage':'other_reviews','not_guaranteed':True}])
                else:
                    yield from offer('await_response',['proposal',pid,'reviews',rev],'審査の返事を待つ',[effect(key(pid,'phase'),'reviewed')],
                                     [{'external_reply':pid,'revision':revision,'stage':'reviews','not_guaranteed':True}])
            elif phase=='reviewed':
                heard=known['reviews'] if revision==known['revision'] else {}
                refresh=[]
                for who,review in heard.items():
                    missing=review.get('missing_knowledge',[])
                    if missing and all(state.get(('knowledge',who,f),False) for f in missing):
                        refresh.append(who)
                    elif missing and state.get(('zone',who))==spec['place']:
                        for fact in missing:
                            if state.get(('knowledge',actor.id,fact),False) and not state.get(('knowledge',who,fact),False):
                                yield from offer('share_knowledge',[who,fact],'聞いた不足情報を伝える',[
                                    {'kind':'knowledge','actor':who,'fact':fact,'value':True}])
                opposed=any(r['stance']!='support' and who not in refresh for who,r in heard.items())
                if opposed:
                    used={v['alternative'] for v in known['versions']}
                    permitted=None
                    if revision==known['revision'] and 'choice_policy' in spec:
                        from engine.deliberation_choice import choice_basis
                        basis=choice_basis(world.deliberation,world,spec,known,actor.id,turn+elapsed)
                        request_weight=max((o['weight'] for o in basis['options'] if o['available'] and o['verb']=='request_decision'),default=0)
                        permitted={tuple(o['args']) for o in basis['options'] if o['available'] and o['verb']=='amend_proposal'
                                   and o['weight']>=request_weight}
                    for alt in spec['alternatives']:
                        if alt['id'] in used or world.deliberation._assessment(world,spec,actor.id,alt['terms'],projection=projection)['stance']!='support': continue
                        if permitted is not None and (pid,alt['id'],rev) not in permitted: continue
                        effects=[effect(key(pid,'phase'),'open'),effect(key(pid,'revision'),revision+1),effect(key(pid,'alternative'),alt['id'])]
                        effects += [effect(key(pid,'heard:'+who),state.get(('zone',who))==spec['place']) for who in spec['reviewers']]
                        yield from offer('amend_proposal',[pid,alt['id'],rev],'聞いた指摘に対応する',effects)
                elif not refresh or state.get(key(pid,'fresh_reviews'),False):
                    yield from offer('request_decision',[pid,rev],'判断を求める',[effect(key(pid,'phase'),'submitted')])
                if refresh and not state.get(key(pid,'fresh_reviews'),False):
                    yield from offer('await_response',['proposal',pid,'reviews',rev],'情報追加後の再審査を待つ',
                        [effect(key(pid,'fresh_reviews'),True)],
                        [{'external_reply':pid,'revision':revision,'stage':'fresh_reviews','not_guaranteed':True}])
            elif phase=='submitted':
                outcomes=[effect(('state',k),v) for k,v in spec['outcomes']['approved'].items()]
                outcomes.append(effect(key(pid,'phase'),'approved'))
                yield from offer('await_response',['proposal',pid,'decision',rev],'相手の判断を待つ',outcomes,
                                 [{'external_reply':pid,'revision':revision,'stage':'approval','not_guaranteed':True}])
        for rid,request in self.requests.items():
            if request['requester']!=actor.id: continue
            spec=world.responses.actions[request['action']]
            if not needed.intersection(map(effect_coordinate,spec['effects'])): continue
            status=state.get(key(rid,'request'),'unasked')
            if status=='unasked' and state.get(('zone',request['responder']))==state.get(('zone',actor.id)):
                yield from offer('request_response',[request['responder'],request['action']],'協力を頼む',[effect(key(rid,'request'),'asked')])
            elif status in {'asked','accept'}:
                if any(('state',k) in state and state[('state',k)]!=v for k,v in spec['preconditions'].items()): continue
                auth=spec.get('authorization')
                if auth and state.get(key(auth['proposal'],'phase'))!=auth['status']: continue
                effects=deepcopy(spec['effects'])+[effect(key(rid,'request'),'completed')]
                assumptions=[{'key':k,'expected':v} for k,v in spec['preconditions'].items() if ('state',k) not in state]
                yield from offer('await_response',['request',rid,'completion','0'],'協力の返事と作業を待つ',effects,
                                 [*assumptions,{'external_reply':rid,'stage':'cooperation','not_guaranteed':True}])

    def fallback(self, world, actor, root, turn):
        for verb,args,_ in (world.deliberation.candidates(world,actor.id,turn=turn) if world.deliberation else []):
            if verb=='request_decision' and args[0] in self.proposals:
                step=self._step(world,actor,verb,args,'承認を決めつけず正式な判断を求める',[],turn,0)
                return {'status':'seeking_judgment','root':root,'steps':[step],'eta':step['duration'],
                        'assumptions':[],'forecast':world.concerns.assessment(world,actor.id),'intermediate_tasks':[]}
        return None

    def can_work(self, actor, sid):
        registered=[rid for rid,r in self.requests.items() if r['responder']==actor and r['action']==sid]
        return not registered or any(self.messages.get(rid,{}).get('status')=='accept' for rid in registered)

    def request_binding(self, actor, args):
        return next((rid for rid,r in self.requests.items() if len(args)==2 and
                     (r['requester'],r['responder'],r['action'])==(actor,args[0],args[1])),None)

    def execute(self, world, actor, action, turn, day):
        if action.verb=='await_response':
            self.wait_duration(world,action.args)
            return 'waited',{'social_wait':{'kind':action.args[0],'reference':action.args[1],'stage':action.args[2],
                                         'revision':action.args[3],'forecast_applied':False}},[]
        if action.verb=='request_response':
            rid=self.request_binding(actor.id,action.args)
            if rid is None or rid in self.messages: return 'invalid',{'reason':'cooperation_request_unavailable'},[]
            spec=self.requests[rid]; peer=world.subjects[spec['responder']]
            if actor.zone!=peer.zone or actor.zone not in world.zones or getattr(peer,'in_transit',False) or peer.vitality not in {'alive','revived'}:
                return 'invalid',{'reason':'cooperation_not_received'},[]
            self.messages[rid]={'status':'delivered','known_reply':None,'sent_at':turn}
            return 'requested',{'social_event':{'request':rid,'from':actor.id,'to':peer.id,'answer':None,
                                             'audience':[actor.id,peer.id],'turn':turn}},[]
        rid,answer=action.args if len(action.args)==2 else ('','')
        spec=self.requests.get(rid); message=self.messages.get(rid)
        if not spec or spec['responder']!=actor.id or not message or message['status']!='delivered' or answer not in {'accept','refuse'}:
            return 'invalid',{'reason':'cooperation_reply_unavailable'},[]
        option=world.responses._option(world,actor,world.responses.actions[spec['action']],turn,day)
        better=any(not x['excluded'] and x['priority']>option['priority'] for x in world.responses.options(world,actor,turn,day))
        if answer=='accept' and (option['excluded'] or better): return 'invalid',{'reason':'cooperation_conflicts_with_own_conditions'},[]
        message['status']=answer; message['answered_at']=turn
        audience=[actor.id]
        if world.subjects[spec['requester']].zone==actor.zone:
            message['known_reply']=answer; audience.append(spec['requester'])
        if answer=='accept': world.responses.active[actor.id]={'action':spec['action'],'since':turn,'request':rid}
        return 'answered',{'social_event':{'request':rid,'from':actor.id,'to':spec['requester'],'answer':answer,
                                         'audience':audience,'turn':turn,'assessment':option}},[]

    def peer_candidates(self, world, actor, turn, day):
        result=[]
        for rid,spec in self.requests.items():
            if spec['responder']!=actor.id or self.messages.get(rid,{}).get('status')!='delivered': continue
            option=world.responses._option(world,actor,world.responses.actions[spec['action']],turn,day)
            better=any(not x['excluded'] and x['priority']>option['priority'] for x in world.responses.options(world,actor,turn,day))
            answer='accept' if not option['excluded'] and not better else 'refuse'
            if 'answer_response' in actor.verbs: result.append((Action('answer_response',(rid,answer)),60))
        return result

    def deliberation_candidates(self,world,actor,turn):
        choices=world.deliberation.candidates(world,actor.id,turn=turn)
        result=[]
        for verb,args,weight in choices:
            pid=args[0]
            if pid not in self.proposals: result.append((verb,args,weight)); continue
            spec=world.deliberation.specs[pid]; known=self.known(world,actor.id,pid)
            if actor.id==spec['proposer'] and world.planner.owns(actor): continue
            if known is None or str(known['revision'])!=args[-1]: continue
            if verb=='decide_proposal' and known['status']!='submitted': continue
            result.append((verb,args,weight))
        return result

    def after_action(self, world, actor, action, result, details, turn):
        if action.verb=='share_knowledge' and result=='shared' and len(action.args)==2 and action.args[1]!='雑談':
            plan=action.meta.get('goal_plan')
            if plan:
                details['social_event']={'kind':'disclosure','from':actor.id,'to':action.args[0],'fact':action.args[1],
                    'audience':[actor.id,action.args[0]],'received_by':[actor.id,action.args[0]],'turn':turn,
                    'binding':{'actor':actor.id,'root':plan['root']}}
        event=details.get('deliberation_event')
        if event and event['proposal'] in self.proposals:
            pid=event['proposal']; spec=world.deliberation.specs[pid]; record=world.deliberation.records[pid]
            plan=action.meta.get('goal_plan')
            if plan: self.links[pid]={'actor':actor.id,'root':plan['root']}
            for who in event['audience']:
                if who not in spec['profiles']: continue
                prior=self.known(world,who,pid)
                status=(event['status'] if event['verb']=='decide_proposal' else 'submitted' if event['verb']=='request_decision' else
                        'open' if event['verb'] in {'submit_proposal','amend_proposal'} or not prior or prior['revision']!=record['revision'] else prior['status'])
                view={'status':status,'revision':record['revision'],'alternative':record['alternative'],'terms_hash':record['terms_hash'],
                      'known_by':{x:record['revision'] for x in event['audience'] if x in spec['profiles']},
                      'reviews':{x:deepcopy(v) for x,v in record['reviews'].items() if who in v['audience']},
                      'versions':deepcopy(record['versions'] if who==spec['proposer'] else
                          [v for v in record['versions'] if v['revision']==record['revision'] or
                           prior and v['revision'] in {old['revision'] for old in prior['versions']}])}
                if prior and prior['revision']==view['revision']: view['known_by']={**prior['known_by'],**view['known_by']}
                self.views.setdefault(who,{})[pid]=view
            details['social_event']={**deepcopy(event),'binding':deepcopy(self.links.get(pid)),
                                     'kind':'proposal','received_by':list(event['audience'])}
        if action.verb=='respond' and result in {'responded','failed','invalid'}:
            for rid,r in self.requests.items():
                if r['responder']==actor.id and r['action']==action.args[0] and self.messages.get(rid,{}).get('status')=='accept':
                    answer='completed' if result=='responded' else 'failed'
                    self.messages[rid]['status']=answer
                    audience=[actor.id]
                    if world.subjects[r['requester']].zone==actor.zone:
                        self.messages[rid]['known_reply']=answer;audience.append(r['requester'])
                    details['social_event']={'request':rid,'from':actor.id,'to':r['requester'],'answer':answer,
                                             'audience':audience,'turn':turn}
        reply=details.get('social_event')
        if reply:
            reply['receipt_id']='social-'+str(len(self.history)+1)
            rid=reply.get('request')
            if rid in self.requests:
                binding=self.requests[rid]
                if action.meta.get('goal_plan'): self.links[rid]={'actor':actor.id,'root':action.meta['goal_plan']['root']}
                reply['binding']=deepcopy(self.links.get(rid)); reply['kind']='cooperation';reply['received_by']=reply['audience']
            self.history.append({'turn':turn,'actor':actor.id,'verb':action.verb,'result':result,**deepcopy(reply)})
            details['social_state']=self.snapshot()

    def snapshot(self):
        return deepcopy({'version':1,'views':self.views,'messages':self.messages,'history':self.history,'links':self.links})

    def validate_snapshot(self,world,snapshot):
        shape(snapshot,{'version','views','messages','history','links'})
        if snapshot['version']!=1 or not isinstance(snapshot['views'],dict) or not set(snapshot['views'])<=set(world.subjects):
            raise ValueError('Invalid social checkpoint views')
        for actor,views in snapshot['views'].items():
            if not isinstance(views,dict) or not set(views)<=set(self.proposals): raise ValueError('Unknown checkpoint proposal')
        if not isinstance(snapshot['messages'],dict) or not set(snapshot['messages'])<=set(self.requests): raise ValueError('Unknown checkpoint request')
        if not isinstance(snapshot['history'],list) or not isinstance(snapshot['links'],dict): raise ValueError('Invalid social checkpoint history')
        if not set(snapshot['links'])<=set(self.proposals)|set(self.requests): raise ValueError('Unknown social plan link')
        for link in snapshot['links'].values():
            shape(link,{'actor','root'})
            if link['actor'] not in world.subjects or link['root'] not in world.goal_rules.definitions: raise ValueError('Invalid social plan link')
        for message in snapshot['messages'].values():
            if (not isinstance(message,dict) or not {'status','known_reply','sent_at'}<=set(message)
                    or message['status'] not in {'delivered','accept','refuse','completed','failed'}
                    or message['known_reply'] not in {None,'accept','refuse','completed','failed'}): raise ValueError('Invalid cooperation checkpoint')

    def restore(self,snapshot):
        for name in ('views','messages','history','links'): setattr(self,name,deepcopy(snapshot[name]))
