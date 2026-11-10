"use strict";

// Preferences are local to this app's browser origin; no node setting is stored here.
globalThis.DashboardRefresh = class DashboardRefresh {
  constructor(load) {
    this.load = load;
    this.pending = false;
    this.timer = null;
    this.buttonTimer = null;
    this.input = document.getElementById("refresh-minutes");
    this.button = document.getElementById("refresh");
    this.note = document.getElementById("refresh-note");
    this.minutes = this.valid(this.read("minutes")) || 1;
    this.input.value = this.minutes;
    this.button.addEventListener("click", () => this.run());
    document.getElementById("save-refresh").addEventListener("click", () => this.save());
    this.input.addEventListener("keydown", event => {
      if (event.key === "Enter") { event.preventDefault(); this.save(); }
    });
    document.addEventListener("visibilitychange", () => this.schedule());
    this.describe();
    this.schedule();
  }
  valid(value) {
    const number = Number(value);
    return Number.isSafeInteger(number) && number >= 1 && Number.isSafeInteger(number * 60000) ? number : null;
  }
  read(name) {
    try { return localStorage.getItem("qbitleap.refresh." + name); } catch (_) { return null; }
  }
  write(name, value) {
    try { localStorage.setItem("qbitleap.refresh." + name, String(value)); } catch (_) { /* Private browsing may disable storage. */ }
  }
  elapsed() {
    return this.lastAttempt === undefined ? Infinity : Math.max(0, Date.now() - this.lastAttempt);
  }

  describe() {
    this.note.textContent = `Automatically refresh every ${this.minutes} minute${this.minutes === 1 ? "" : "s"} while this page is visible. Minimum: 1 minute.`;
  }
  save() {
    const minutes = this.valid(this.input.value);
    this.input.setCustomValidity(minutes === null ? "Enter a whole number of minutes, at least 1." : "");
    if (minutes === null) { this.input.reportValidity(); return; }
    this.minutes = minutes;
    this.write("minutes", minutes);
    this.describe();
    this.schedule();
  }
  schedule() {
    clearTimeout(this.timer);
    clearTimeout(this.buttonTimer);
    this.timer = this.buttonTimer = null;
    const cooldown = Math.max(0, 60000 - this.elapsed());
    this.button.disabled = this.pending || document.hidden || cooldown > 0;
    if (document.hidden || this.pending) return;
    if (cooldown > 0) this.buttonTimer = setTimeout(() => this.schedule(), cooldown);
    const remaining = Math.max(0, this.minutes * 60000 - this.elapsed());
    this.timer = setTimeout(() => this.run(true), Math.min(remaining, 2147483647));
  }
  async run(automatic = false) {
    if (this.pending || document.hidden || this.elapsed() < (automatic ? this.minutes * 60000 : 60000)) {
      this.schedule(); return;
    }
    this.pending = true;
    this.lastAttempt = Date.now();
    this.schedule();
    try { await this.load(); }
    finally { this.pending = false; this.schedule(); }
  }
};
