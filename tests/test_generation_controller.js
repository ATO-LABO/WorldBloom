"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../viewer/static/generation.js"), "utf8");

function element() {
  return {textContent: "", hidden: false, disabled: false, checked: false, children: [],
    classList: {toggle() {}}, parentElement: {scrollTop: 0},
    append(...items) {this.children.push(...items);},
    replaceChildren(...items) {this.children = items;},
    setAttribute() {}, removeAttribute() {}, querySelector() {return element();},
    querySelectorAll() {return [];}};
}

async function monitor({state = "running", phase = "preparing", status = 404} = {}) {
  const fields = new Map(), get = key => {
    if (!fields.has(key)) fields.set(key, element());
    return fields.get(key);
  };
  let current = {job_id: "job-test", run_id: "run-test", output_id: "out-test",
    kind: "narrate", state, phase, progress: {total: 1, completed: 0}};
  let outputAvailable = false;
  const requests = [], timers = [];
  const root = {tagName: "SECTION", dataset: {initial: JSON.stringify({job: current})},
    querySelector: selector => get(selector), querySelectorAll: () => []};
  vm.runInNewContext(source, {
    document: {querySelector: () => root, createElement: element, body: {append() {}}},
    sessionStorage: {setItem() {}}, window: {}, location: {},
    clearTimeout() {}, setTimeout(fn) {timers.push(fn); return timers.length;},
    fetch: async (url, options) => {
      requests.push({url, method: options?.method || "GET"});
      if (url === "/api/jobs/job-test") return {ok: true, json: async () => current};
      assert.equal(url, "/api/outputs/out-test");
      return outputAvailable
        ? {ok: true, json: async () => ({output_id: "out-test", entries: [],
            request: {pipeline: "facts_v1"}, completion_kind: "prompt_only"})}
        : {ok: false, status, json: async () => ({message: "生成版がありません"})};
    }
  });
  await new Promise(setImmediate);
  return {get, requests, timers, async finishPreparation() {
    current = {...current, state: "succeeded", phase: "generating",
      completion_kind: "prompt_only", progress: {total: 1, completed: 1}};
    outputAvailable = true;
    assert.equal(timers.length, 1, "the same job must keep being observed");
    await timers.shift()();
  }};
}

test("preparation may precede output creation; observe the same job without an error or resubmission", async () => {
  const ui = await monitor();
  assert.equal(ui.get("[data-gen-error]").textContent, "");
  assert.equal(ui.requests.length, 2);
  await ui.finishPreparation();
  assert.equal(ui.get("[data-gen-error]").textContent, "");
  assert.equal(ui.get("[data-gen-message]").textContent, "プロンプト保存のみ（文章は未生成）");
  assert.equal(ui.get("[data-gen-count]").textContent, "処理済み 1 / 1件（100%）");
  assert.ok(ui.requests.every(r => r.method === "GET"), "monitoring cannot start another generation");
});

test("missing output after preparation stays an error", async () => {
  const ui = await monitor({phase: "generating"});
  assert.match(ui.get("[data-gen-error]").textContent, /生成版がありません/);
});

test("missing output for a terminal job stays an error even if its phase is preparing", async () => {
  const ui = await monitor({state: "failed"});
  assert.match(ui.get("[data-gen-error]").textContent, /生成版がありません/);
});

test("preparation never hides authorization or server errors", async () => {
  for (const status of [403, 503]) {
    const ui = await monitor({status});
    assert.match(ui.get("[data-gen-error]").textContent, /生成版がありません/);
  }
});
