"""Synthetic codec checks only; no native or application output qualification."""
from dataclasses import replace
import hashlib
import json
import struct
import unittest

from engine.calendar_generation import (
    MAX_CHECKPOINT_BYTES, MAX_GENERATION_BYTES, MAX_HEADER_BYTES, MAX_ORDINAL,
    GenerationError, GenerationRef, SelectedGeneration, checkpoint_bytes,
    checkpoint_payload, prepare_generation, decode_generation, leaf_for,
)
from execution.provenance import canonical

COMPAT = {'family': 'synthetic', 'provider': 'pinned', 'python': '3.12.14', 'platform': 'fixture'}
STREAM = 'a' * 32


def prepared(state=None, **kw):
    return prepare_generation(checkpoint_bytes({'tick': 0} if state is None else state, COMPAT),
                              expected_compatibility=COMPAT, stream_id=kw.pop('stream_id', STREAM),
                              ordinal=kw.pop('ordinal', 0), **kw)


def ref_for(data, original):
    return replace(original, size=len(data), sha256=hashlib.sha256(data).hexdigest())


def modify_header(generation, callback, *, raw=None):
    magic, header_size, payload_size = struct.unpack_from('<8sIQ', generation.data)
    h=json.loads(generation.data[20:20+header_size]);callback(h)
    index=canonical(h) if raw is None else raw
    data=struct.pack('<8sIQ',magic,len(index),payload_size)+index+generation.checkpoint
    return data,ref_for(data,generation.ref)


class GenerationCodecTests(unittest.TestCase):
    def test_original_checkpoint_serialization_is_unchanged(self):
        state={'tick':7,'unicode':'試験','rows':[{'x':1}]}
        payload={'compatibility':COMPAT,'state':state}
        old=canonical({'payload':payload,'sha256':hashlib.sha256(canonical(payload)).hexdigest()})
        self.assertEqual(old,checkpoint_bytes(state,COMPAT))
        g=prepared(state);self.assertEqual(old,g.checkpoint)
        self.assertEqual(g,decode_generation(g.data,expected=g.ref,expected_compatibility=COMPAT))

    def test_noncanonical_checkpoint_bytes_are_preserved(self):
        envelope=json.loads(checkpoint_bytes({'tick':1},COMPAT))
        raw=json.dumps(envelope,ensure_ascii=False,indent=2).replace('\n','\r\n').encode()+b'\r\n'
        g=prepare_generation(raw,expected_compatibility=COMPAT,stream_id=STREAM,ordinal=0)
        self.assertEqual(raw,g.checkpoint)
        self.assertEqual(raw,decode_generation(g.data,expected=g.ref,expected_compatibility=COMPAT).checkpoint)

    def test_expected_hash_rejects_coherently_rehashed_alternate(self):
        a=prepared({'tick':1});b=prepared({'tick':2})
        self.assertEqual(len(a.data),len(b.data))
        with self.assertRaises(GenerationError):
            decode_generation(b.data,expected=a.ref,expected_compatibility=COMPAT)

    def test_reference_identity_and_size_are_independently_checked(self):
        g=prepared()
        for ref in (replace(g.ref,size=g.ref.size+1),replace(g.ref,stream_id='b'*32),replace(g.ref,ordinal=1)):
            with self.subTest(ref=ref),self.assertRaises(GenerationError):
                decode_generation(g.data,expected=ref,expected_compatibility=COMPAT)

    def test_max_checkpoint_uses_separate_framing_budget(self):
        base=len(checkpoint_bytes({'pad':''},COMPAT))
        raw=checkpoint_bytes({'pad':'x'*(MAX_CHECKPOINT_BYTES-base)},COMPAT)
        self.assertEqual(MAX_CHECKPOINT_BYTES,len(raw))
        g=prepare_generation(raw,expected_compatibility=COMPAT,stream_id=STREAM,ordinal=0)
        self.assertGreater(len(g.data),MAX_CHECKPOINT_BYTES)
        self.assertLessEqual(len(g.data),MAX_GENERATION_BYTES)
        self.assertEqual(raw,decode_generation(g.data,expected=g.ref,expected_compatibility=COMPAT).checkpoint)
        with self.assertRaises(GenerationError): checkpoint_payload(raw+b' ',COMPAT)
        with self.assertRaises(GenerationError): checkpoint_bytes({'pad':'x'*(MAX_CHECKPOINT_BYTES-base+1)},COMPAT)

    def test_missing_extra_wrong_type_and_nonfinite_checkpoint_fields(self):
        good=json.loads(checkpoint_bytes({'tick':0},COMPAT))
        bad=[{},[],{'payload':good['payload']},dict(good,extra=1),dict(good,sha256='0'*64),
             {'payload':{'compatibility':COMPAT},'sha256':'0'*64}]
        for value in bad:
            with self.subTest(value=value),self.assertRaises(GenerationError): checkpoint_payload(canonical(value),COMPAT)
        for raw in (b'{"payload":1,"payload":2,"sha256":"x"}',b'{"x":NaN}',b'\xff'):
            with self.subTest(raw=raw),self.assertRaises(GenerationError):checkpoint_payload(raw,COMPAT)
        with self.assertRaises(ValueError):checkpoint_bytes({'bad':float('nan')},COMPAT)

    def test_stale_compatibility_rejected(self):
        g=prepared()
        for field in COMPAT:
            stale=dict(COMPAT);stale[field]='changed'
            with self.subTest(field=field),self.assertRaises(GenerationError):
                decode_generation(g.data,expected=g.ref,expected_compatibility=stale)

    def test_strict_metadata_rejects_schema_mutations_even_with_new_outer_hash(self):
        g=prepared()
        mutations=[lambda h:h.update(extra=1),lambda h:h.pop('kind'),lambda h:h.update(schema=True),
                   lambda h:h.update(schema=2),lambda h:h.update(kind='delta'),lambda h:h.update(ordinal=True),
                   lambda h:h.update(stream_id='../bad'),lambda h:h.update(checkpoint_size=True),
                   lambda h:h.update(checkpoint_sha256='0'*64),lambda h:h.update(compatibility_sha256='0'*64),
                   lambda h:h.update(previous={'bad':1}),lambda h:h.update(forked_from={'bad':1})]
        for index,mutation in enumerate(mutations):
            data,ref=modify_header(g,mutation)
            with self.subTest(index=index),self.assertRaises(GenerationError):
                decode_generation(data,expected=ref,expected_compatibility=COMPAT)

    def test_duplicate_noncanonical_and_nonfinite_metadata_rejected(self):
        g=prepared();size=struct.unpack_from('<8sIQ',g.data)[1];index=g.data[20:20+size]
        for raw in (b' '+index,index[:-1]+b',"schema":1}',index.replace(b'"schema":1',b'"schema":NaN')):
            data,ref=modify_header(g,lambda h:None,raw=raw)
            with self.subTest(raw=raw[:15]),self.assertRaises(GenerationError):
                decode_generation(data,expected=ref,expected_compatibility=COMPAT)

    def test_truncation_trailing_bytes_magic_and_lengths_rejected(self):
        g=prepared();magic,header,payload=struct.unpack_from('<8sIQ',g.data)
        bad=[g.data[:-1],g.data+b'x',b'BADMAGIC'+g.data[8:]]
        for h,p in [(0,payload),(MAX_HEADER_BYTES+1,payload),(header,0),(header,MAX_CHECKPOINT_BYTES+1),
                    (header,(1<<64)-1),(header+1,payload)]:
            bad.append(struct.pack('<8sIQ',magic,h,p)+g.data[20:])
        for data in bad:
            with self.subTest(size=len(data)),self.assertRaises(GenerationError):
                decode_generation(data,expected=ref_for(data,g.ref),expected_compatibility=COMPAT)

    def test_lineage_and_continuation_are_exact(self):
        a=prepared();b=prepared({'tick':1},ordinal=1,previous=a.ref)
        fork=prepared({'tick':0},stream_id='b'*32,forked_from=a.ref)
        self.assertEqual(a.ref,b.previous);self.assertEqual(a.ref,fork.forked_from)
        for kwargs in ({'ordinal':1},{'previous':a.ref},{'ordinal':2,'previous':a.ref},
                       {'ordinal':1,'previous':fork.ref},{'forked_from':a.ref},
                       {'ordinal':1,'previous':a.ref,'forked_from':fork.ref}):
            with self.subTest(kwargs=kwargs),self.assertRaises(GenerationError):prepared(**kwargs)

    def test_reference_and_leaf_reject_aliases_bool_and_out_of_range(self):
        g=prepared();self.assertLessEqual(len(g.leaf),255)
        for ref in (replace(g.ref,size=True),replace(g.ref,size=MAX_GENERATION_BYTES+1),
                    replace(g.ref,ordinal=True),replace(g.ref,ordinal=MAX_ORDINAL+1),
                    replace(g.ref,sha256='A'*64),replace(g.ref,stream_id='A'*32)):
            with self.subTest(ref=ref),self.assertRaises(GenerationError):leaf_for(ref)
        for leaf in ('../'+g.leaf,g.leaf+' ',g.leaf.upper(),'CON',g.leaf.replace('.wbg',':ads')):
            with self.subTest(leaf=leaf),self.assertRaises(GenerationError):SelectedGeneration(leaf,g.ref).validate()

    def test_loaded_state_is_a_fresh_value(self):
        g=prepared({'nested':{'value':1}});state=g.state(COMPAT);state['nested']['value']=99
        self.assertEqual(1,g.state(COMPAT)['nested']['value'])

    def test_largest_schema_metadata_fits_fixed_header_bound(self):
        ref=GenerationRef('f'*64,MAX_GENERATION_BYTES,'b'*32,MAX_ORDINAL-1)
        g=prepared(stream_id='b'*32,ordinal=MAX_ORDINAL,previous=ref)
        header=struct.unpack_from('<8sIQ',g.data)[1]
        self.assertLess(header,MAX_HEADER_BYTES)
