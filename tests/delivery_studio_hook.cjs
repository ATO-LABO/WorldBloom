'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {createUpdateHook, worldbloomLaunchEnvironment} = require('../tools/delivery/studio-hook.cjs');
(async () => {
  let calls = 0;
  const forbidden = () => { calls++; throw new Error('unexpected execution'); };
  assert.deepEqual(await createUpdateHook({}, forbidden)(), {status: 'disabled', can_launch: true});
  assert.equal(calls, 0);
  const config = {enabled: true, python: path.resolve('python.exe'), configPath: path.resolve('disabled.json'), moduleRoot: path.resolve('.')};
  let captured;
  const execute = (command, args, options, done) => {
    captured = {command, args, options};
    done(null, JSON.stringify({status: 'available', can_launch: true}));
  };
  assert.equal((await createUpdateHook(config, execute)({apply: true})).status, 'available');
  assert.equal(captured.options.shell, false);
  assert.equal(captured.options.windowsHide, true);
  assert(captured.args.includes('--apply'));
  assert.equal((await createUpdateHook(config, (_,__,___,done) => done(new Error('blocked'), '{"status":"blocked","can_launch":false}'))()).can_launch, false);
  await assert.rejects(createUpdateHook(config, (_,__,___,done) => done(new Error('timeout'), '{"status":"installed","can_launch":true}'))(), /unknown/);
  await assert.rejects(createUpdateHook(config, (_,__,___,done) => done(null, 'partial'))(), /unavailable/);
  assert.deepEqual(worldbloomLaunchEnvironment({EXAMPLE: 'kept'}), {EXAMPLE: 'kept'});
  const env = worldbloomLaunchEnvironment({}, {enabled: true, state: path.resolve('state')});
  assert.equal(env.WORLDBLOOM_DELIVERY_GUARD, '1');
  assert.equal(env.WORLDBLOOM_DELIVERY_STATE, path.resolve('state'));
  console.log('studio hook checks passed; no real process or timer started');
})().catch(error => { console.error(error); process.exitCode = 1; });
