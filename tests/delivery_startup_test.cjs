'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {createStartupCoordinator} = require('../tools/delivery/studio-startup.example.cjs');
const state = path.resolve('fixture-update-state');

(async () => {
  let calls = 0;
  const disabled = createStartupCoordinator({}, () => { ++calls; throw Error('must not run'); });
  assert.equal((await disabled.onStudioStarted()).status, 'disabled');
  const oldEnvironment = {fixture: 'unchanged'};
  assert.deepEqual((await disabled.beforeWorldbloomLaunch(oldEnvironment)).environment, oldEnvironment);
  assert.equal(calls, 0);
  assert.throws(() => createStartupCoordinator({enabled: true}, () => {}));
  assert.throws(() => createStartupCoordinator({periodic_minutes: 15}, () => {}));

  const operations = [];
  const normal = createStartupCoordinator({enabled: true, state}, async args => {
    operations.push(args); return {status: 'available', can_launch: true};
  });
  await Promise.all([normal.onStudioStarted(), normal.onStudioStarted()]);
  assert.deepEqual(operations, [{apply: false}]);
  const ready = await normal.beforeWorldbloomLaunch({fixture: 'value'});
  assert.deepEqual(operations, [{apply: false}, {apply: false}]);
  assert.equal(ready.environment.WORLDBLOOM_DELIVERY_GUARD, '1');
  assert.equal(ready.environment.WORLDBLOOM_DELIVERY_STATE, state);
  assert.equal(ready.environment.fixture, 'value');

  let resolve;
  const concurrent = [];
  const explicit = createStartupCoordinator({enabled: true, auto_apply: true, state}, async args => {
    concurrent.push(args);
    if (!args.apply) await new Promise(r => { resolve = r; });
    return {status: args.apply ? 'installed' : 'available', can_launch: true};
  });
  const start = explicit.onStudioStarted();
  const launch = explicit.beforeWorldbloomLaunch({});
  await Promise.resolve();
  assert.deepEqual(concurrent, [{apply: false}]);
  resolve(); await Promise.all([start, launch]);
  assert.deepEqual(concurrent, [{apply: false}, {apply: true}]);

  for (const result of [{status: 'recovery_required', can_launch: false}, null]) {
    const blocked = createStartupCoordinator({enabled: true, state}, async () => result);
    const decision = await blocked.beforeWorldbloomLaunch({});
    assert.equal(decision.can_launch, false);
    assert.equal(decision.environment, undefined);
  }
  const rejected = createStartupCoordinator({enabled: true, state}, async () => { throw Error('offline unknown'); });
  assert.equal((await rejected.beforeWorldbloomLaunch({})).can_launch, false);
  console.log('startup coordinator passed: default disabled, check-only startup, no timers or real process');
})().catch(error => { console.error(error); process.exitCode = 1; });
