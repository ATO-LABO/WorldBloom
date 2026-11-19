/* Inert integration module. No timer, server mutation, or process on import. */
'use strict';
const {execFile} = require('node:child_process');
const path = require('node:path');

function createUpdateHook(config, execute = execFile) {
  return async function checkOnce({apply = false} = {}) {
    if (config.enabled !== true) return {status: 'disabled', can_launch: true};
    if (!path.isAbsolute(config.python || '') || !path.isAbsolute(config.configPath || '') ||
        !path.isAbsolute(config.moduleRoot || '')) throw new Error('invalid update integration paths');
    const args = ['-B', '-m', 'wb_delivery', '--config', config.configPath];
    if (apply) args.push('--apply');
    return new Promise((resolve, reject) => {
      execute(config.python, args, {cwd: config.moduleRoot, windowsHide: true, shell: false,
          timeout: 180000, maxBuffer: 65536}, (error, stdout) => {
        let result;
        try { result = JSON.parse(stdout); } catch (_) { return reject(new Error('update result unavailable')); }
        if (!result || typeof result.status !== 'string' || typeof result.can_launch !== 'boolean') {
          return reject(new Error('invalid update result'));
        }
        if (error && !(result.status === 'blocked' && result.can_launch === false)) {
          return reject(new Error('update outcome unknown'));
        }
        // A structured blocked result remains blocked even when the child exits nonzero.
        resolve(result);
      });
    });
  };
}

function worldbloomLaunchEnvironment(base, {enabled = false, state} = {}) {
  if (enabled !== true) return {...base};
  if (!path.isAbsolute(state || '')) throw new Error('invalid update state path');
  return {...base, WORLDBLOOM_DELIVERY_GUARD: '1', WORLDBLOOM_DELIVERY_STATE: state};
}

module.exports = {createUpdateHook, worldbloomLaunchEnvironment};
