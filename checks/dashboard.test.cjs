"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../app/service/static/refresh.js"), "utf8");

function fixture(storage = new Map()) {
  let now = 1000000, serial = 0, calls = [], resolveLoad = null;
  const timers = new Map(), listeners = new Map(), elements = new Map();
  for (const id of ["refresh-minutes", "refresh", "refresh-note", "save-refresh"]) {
    elements.set(id, {value: "1", disabled: false, textContent: "", events: {},
      addEventListener(name, action) { this.events[name] = action; },
      setCustomValidity(value) { this.validity = value; }, reportValidity() {}});
  }
  const document = {hidden: false, getElementById: id => elements.get(id),
    addEventListener: (name, action) => listeners.set(name, action)};
  const context = {document, Date: {now: () => now}, Number, Math,
    localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
    setTimeout: (action, delay) => { const id = ++serial; timers.set(id, {action, at: now + delay}); return id; },
    clearTimeout: id => timers.delete(id)};
  vm.createContext(context); vm.runInContext(source, context);
  const refresh = new context.DashboardRefresh(async () => { calls.push(now); if (resolveLoad) await resolveLoad; });
  async function tick(milliseconds) {
    const until = now + milliseconds;
    for (let limit = 0; limit < 100; limit++) {
      const entries = [...timers].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at);
      if (!entries.length) break;
      const [id, task] = entries[0]; timers.delete(id); now = task.at; task.action();
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
      if (limit === 99) throw new Error("Timer loop");
    }
    now = until;
  }
  return {refresh, calls, elements, tick, storage,
    visibility(hidden) { document.hidden = hidden; listeners.get("visibilitychange")(); },
    input(value) { elements.get("refresh-minutes").value = value; elements.get("save-refresh").events.click(); },
    click() { elements.get("refresh").events.click(); },
    pause(promise) { resolveLoad = promise; }};
}

(async () => {
  const one = fixture(); await one.tick(0); assert.equal(one.calls.length, 1);
  one.click(); one.visibility(true); await one.tick(20000); one.visibility(false);
  await one.tick(39999); assert.equal(one.calls.length, 1);
  await one.tick(1); assert.equal(one.calls.length, 2);
  for (const input of ["0", "-1", "0.5", "1.5", "bad", "Infinity"]) {
    one.input(input); assert.ok(one.elements.get("refresh-minutes").validity);
  }
  one.input("3"); assert.equal(one.refresh.minutes, 3);
  await one.tick(179999); assert.equal(one.calls.length, 2);
  await one.tick(1); assert.equal(one.calls.length, 3);
  one.visibility(true); await one.tick(600000); assert.equal(one.calls.length, 3);
  one.visibility(false); await one.tick(0); assert.equal(one.calls.length, 4);
  assert.ok(one.calls.slice(1).every((time, i) => time - one.calls[i] >= 60000));

  const stored = new Map([["qbitleap.refresh.minutes", "0"]]);
  const two = fixture(stored); assert.equal(two.refresh.minutes, 1);
  let finish; two.pause(new Promise(resolve => { finish = resolve; }));
  await two.tick(0); await two.tick(180000); two.click(); assert.equal(two.calls.length, 1);
  finish(); await new Promise(resolve => setImmediate(resolve)); await two.tick(0);
  assert.equal(two.calls.length, 2);
  assert.ok(two.calls[1] - two.calls[0] >= 60000);
  console.log("PASS: minute minimum, custom interval, hidden-page pause, return timing, corrupt preferences and no overlapping fetches.");
})().catch(error => { console.error(error); process.exitCode = 1; });
