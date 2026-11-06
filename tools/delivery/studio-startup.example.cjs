/* Local integration proposal only; no registration, timer, spawn or I/O on import. */
'use strict';
const path = require('node:path');
const {worldbloomLaunchEnvironment} = require('./studio-hook.cjs');

function createStartupCoordinator(config = {}, checkOnce) {
  const enabled = config.enabled === true;
  if (config.periodic_minutes != null) throw new Error('periodic checking is not enabled by this proposal');
  if (enabled && (typeof checkOnce !== 'function' || !path.isAbsolute(config.state || ''))) {
    throw new Error('invalid startup update configuration');
  }
  let startup;
  let inFlight;
  const disabled = () => ({status: 'disabled', can_launch: true});
  function checked(apply) {
    if (!enabled) return Promise.resolve(disabled());
    if (inFlight) {
      if (inFlight.apply === apply) return inFlight.promise;
      return inFlight.promise.then(result => result.can_launch ? checked(apply) : result);
    }
    const current = {apply, promise: null};
    current.promise = Promise.resolve().then(() => checkOnce({apply})).then(result => {
      if (!result || typeof result.status !== 'string' || typeof result.can_launch !== 'boolean') {
        throw new Error('unknown update outcome');
      }
      return result;
    }).catch(() => ({status: 'blocked', can_launch: false, reason: 'update_check_unavailable'}))
      .finally(() => { if (inFlight === current) inFlight = null; });
    inFlight = current;
    return current.promise;
  }
  return {
    onStudioStarted() {
      // Initial operation is check-only. Multiple startup notifications share one call.
      if (!enabled || config.startup_check === false) return Promise.resolve(disabled());
      if (!startup) startup = checked(false);
      return startup;
    },
    async beforeWorldbloomLaunch(baseEnvironment) {
      const result = await checked(config.auto_apply === true);
      if (!result.can_launch) return result;
      return {...result, environment: worldbloomLaunchEnvironment(baseEnvironment, {
        enabled, state: config.state,
      })};
    },
  };
}

module.exports = {createStartupCoordinator};
