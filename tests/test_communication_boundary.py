from pathlib import Path
from copy import deepcopy
import json,tempfile,unittest
from engine.actions import Action
from engine.sim import Simulation
from engine import communication_boundary
from engine.decision_provider import provider_snapshot,staged_provider_restore
from gapengine.communication_repeat import VERSION
from gapengine.festival_policy import build_policies
from scripts.cultural_festival_probe import load

class CompletionBoundaryTests(unittest.TestCase):
    def fixture(self,out):
        w,people=load();organizer=w.slot_contract['actions']['sound_check']['actor']
        cfg={'version':VERSION,'epoch':'boundary-fixture','completion_mode':'explicit','actor_tendencies':{organizer:'reminder'}}
        policies=build_policies(w.slot_contract,communication_config=cfg)
        sim=Simulation(0,w,people,out,policies=policies);rt=sim.slot_runtime;rows=[]
        rt.writer=type('Writer',(),{'write':lambda _,row:rows.append(row)})()
        sim.turn=1;sim.slot=w.slots[0]
        return sim,rt,people[organizer],people[rt.lead],policies,cfg,rows

    def send(self,sim,rt,actor,peer,tick):
        sim.turn=tick+1;sim.slot=sim.world.slots[min(tick,len(sim.world.slots)-1)]
        actor.slot_context={'perceptions':[]};action=Action('dialogue',(peer.id,'出演','request','contact'))
        meta=actor.policy.communication_repeat.assess(actor.id,action,actor.slot_context,tick)
        self.assertTrue(meta['allowed']);action.meta['decision_basis']={'selected':{'communication_repeat':meta}}
        actor.policy.record(actor,action);rt.start(actor,action,1)
        pending=rt.lifecycle.interrupt(actor.id);sim.turn=tick+2;sim.slot=sim.world.slots[min(tick+1,len(sim.world.slots)-1)]
        rt.complete(pending);actor.slot_context={'perceptions':deepcopy(rt.perceptions[actor.id])}
        communication_boundary.flush(actor,tick+1);rt.perceptions[actor.id].clear()
        return meta,pending

    def test_reminder_reuses_original_request_and_payload_at_completion(self):
        with tempfile.TemporaryDirectory() as d:
            sim,rt,actor,peer,policies,cfg,rows=self.fixture(Path(d))
            first,_=self.send(sim,rt,actor,peer,0);original=rt.state['requests'][0]['id']
            repeated,_=self.send(sim,rt,actor,peer,5)
            self.assertEqual(repeated['classification'],'reminder');self.assertEqual(len(rt.state['requests']),1)
            self.assertEqual(actor.policy.communication_repeat.sent[repeated['key']]['request_id'],original)
            completed=rows[-1]['details'];self.assertEqual(completed['communication_repeat'],repeated)
            self.assertEqual(completed['canonical_request_id'],original)
            snapshot=provider_snapshot(actor.policy)
            restored=staged_provider_restore(build_policies(sim.world.slot_contract,communication_config=cfg)[actor.id],snapshot)
            self.assertEqual(provider_snapshot(restored),snapshot)

    def test_repeated_acceptance_does_not_apply_or_speak_again(self):
        with tempfile.TemporaryDirectory() as d:
            sim,rt,actor,peer,_,_,rows=self.fixture(Path(d));self.send(sim,rt,actor,peer,0)
            sim.turn=3;sim.slot=sim.world.slots[2]
            action=next(a for a,_ in rt.candidates(peer) if a.verb=='dialogue' and a.args[0]==actor.id and a.args[2]=='accept' and a.args[3]=='contact')
            rt.start(peer,action,1);pending=rt.lifecycle.interrupt(peer.id);sim.turn=4;rt.complete(pending)
            before=deepcopy(rt.state);speech=len(rt.perceptions[actor.id]);sim.turn=5;rt.complete(pending)
            self.assertEqual(rt.state,before);self.assertEqual(len(rt.state['responses']),1)
            self.assertEqual(len(rt.perceptions[actor.id]),speech)
            self.assertEqual(rows[-1]['details']['reason'],'request_already_answered')

    def test_unowned_metadata_and_update_without_content_refuse(self):
        from engine.communication_boundary import selected,content
        with tempfile.TemporaryDirectory() as d:
            sim,rt,actor,peer,_,_,_=self.fixture(Path(d));action=Action('dialogue',(peer.id,'出演','request','contact'))
            meta=actor.policy.communication_repeat.assess(actor.id,action,{},0)
            action.meta['decision_basis']={'selected':{'communication_repeat':meta}}
            with self.assertRaises(ValueError):selected(actor,action,0)
            meta['classification']='update';meta['evidence_refs']=['missing-content']
            with self.assertRaises(ValueError):content(actor,meta)

    def test_final_flush_and_saved_complete_policy_state(self):
        with tempfile.TemporaryDirectory() as d:
            w,people=load();cfg={'version':VERSION,'epoch':'final-flush','completion_mode':'explicit'}
            policies=build_policies(w.slot_contract,communication_config=cfg)
            sim=Simulation(0,w,people,Path(d),policies=policies);sim.run()
            saved=json.loads((Path(d)/'communication-state.json').read_text())
            self.assertTrue(all(s['last_tick']==len(w.slots)-1 for s in saved.values()))
            self.assertTrue(all(not s['communication_repeat']['pending'] for s in saved.values()))
            self.assertIn('communication_state_sha256',sim.run_record())
            header=json.loads((Path(d)/'layers.jsonl').read_text(encoding='utf-8').splitlines()[0]);self.assertIn('communication_providers',header)
