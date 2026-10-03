'use strict';
const assert = require('node:assert/strict');
const S = require('../viewer/static/local-status.js');
const history = [{at: 100, devices: [{id: 'a', temperature_c: 63}, {id: 'b', temperature_c: 40}]},
  {at: 102, devices: []}, {at: 104, devices: [{id: 'a', temperature_c: 56}]}];
assert.deepEqual(S.historyFor(history, 'a', 100, 104).map(p => p.value), [63, null, 56]);
assert.deepEqual(S.historyFor(history, 'b', 100, 104).map(p => p.value), [40, null, null]);
assert.deepEqual(S.historyFor(history, 'a', 102, 104).map(p => p.at), [102, 104]);
const cooling = Array.from({length: 31}, (_, i) => ({at: 100 + i * 2, value: 63 - i * 7 / 30}));
assert.equal(S.trend(cooling).delta, -7);
assert.match(S.trend(cooling).label, /−7°C/);
assert.equal(S.trend(cooling.slice(1)).delta, null, 'insufficient history must not fabricate a minute trend');
const warming = cooling.map(p => ({at: p.at, value: 120 - p.value}));
assert.equal(S.trend(warming).delta, 7);
const missing = cooling.map((p, i) => i === 12 ? {...p, value: null} : p);
assert.equal(S.trend(missing).delta, null, 'never compare across sensor loss');
assert.equal(S.segments(missing).length, 2, 'never draw through a missing sensor reading');
assert.equal(S.segments([{at: 1, value: 40}, {at: 20, value: 60}]).length, 2, 'long elapsed gaps stay separate');
assert.equal(S.trend([{at: 160, value: null}]).delta, null);

(async () => {
  const scheduled = [], results = [], errors = [];
  let active = true, resolve, reject, count = 0, aborted = false;
  const load = signal => {
    count++;
    signal.addEventListener('abort', () => { aborted = true; });
    return new Promise((res, rej) => { resolve = res; reject = rej; });
  };
  const poller = new S.Poller(load, {active: () => active, success: v => results.push(v), error: e => errors.push(e),
    schedule: f => (scheduled.push(f), scheduled.length), cancel: () => {}});
  poller.start();
  assert.equal(count, 1); assert.equal(scheduled.length, 0, 'no interval while a fetch is outstanding');
  poller.run(poller.version); assert.equal(count, 1, 'concurrent polling is suppressed');
  resolve({reading: 1}); await new Promise(r => setImmediate(r));
  assert.equal(scheduled.length, 1); assert.equal(results.length, 1);
  scheduled.shift()(); assert.equal(count, 2);
  active = false; poller.stop(); assert.equal(aborted, true);
  resolve({reading: 2}); await new Promise(r => setImmediate(r));
  assert.equal(results.length, 1, 'closing discards late results');
  assert.equal(scheduled.length, 0, 'closing never rearms a timer');
  active = true; poller.start(); reject(new Error('timeout'));
  await new Promise(r => setImmediate(r));
  assert.equal(errors.length, 1); assert.equal(scheduled.length, 1, 'failures stay visible and can recover');
  poller.stop();
  console.log('GPU separation, time windows, missing data, trend direction and polling lifecycle: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
