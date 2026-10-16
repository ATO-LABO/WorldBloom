// Run the repository's offline JavaScript contracts without external fixtures.
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

const root = path.resolve(__dirname, '..');
const tests = fs.readdirSync(path.join(root, 'tests'))
  .filter(name => name.endsWith('.cjs') || /^test_.*\.js$/.test(name)).sort();
let failed = 0;
for (const name of tests) {
  console.log(`\nRunning ${name}`);
  const args = name.endsWith('.js') ? ['--test'] : [];
  args.push(path.join(root, 'tests', name));
  if (name === 'raw_workspace_ui.cjs') {
    args.push(path.join(root, 'viewer', 'static', 'raw-workspace.js'));
  }
  const result = spawnSync(process.execPath, args, {
    cwd: root, stdio: 'inherit', timeout: 30000,
  });
  if (result.error) console.error(result.error.message);
  if (result.status !== 0 || result.error) failed++;
}
console.log(`\nJavaScript: ${tests.length} scripts, ${failed} failed.`);
process.exitCode = failed ? 1 : 0;
