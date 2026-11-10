"use strict";
const text = (id, value) => { document.getElementById(id).textContent = String(value ?? "—"); };
const networkSwitch = document.getElementById("network-active");
const unlockButton = document.getElementById("unlock-control");
let state = null, controlReady = false, controlPending = false, csrf = null;

function render() {
  networkSwitch.disabled = !state || !controlReady || controlPending;
  networkSwitch.checked = state?.peerNetworkingEnabled === true;
  text("network-state", !state ? "Unknown" : state.peerNetworkingEnabled ? "On" : "Off");
  if (!state) return;
  for (const id of ["network", "blocks", "headers", "connections"]) text(id, state[id]);
  text("txindex", state.txindexSynced ? `Indexed to block ${state.txindexHeight ?? state.blocks}` : `Indexing block ${state.txindexHeight ?? "unknown"}`);
  const paused = !state.peerNetworkingEnabled;
  text("ready", paused ? "Sync paused" : state.chainAndIndexReady ? "Synchronized" : state.connections === 0 ? "Connecting to peers" : "Catching up");
  text("message", paused ? "Sync paused. Peer networking is off." : state.chainAndIndexReady ? "Your node is synchronized." : state.connections === 0 ? "Connecting to Qbit peers…" : `Syncing block ${state.blocks} of ${state.headers} known headers.`);
  document.getElementById("setup").hidden = !paused;
  text("sync-detail", `Current block: ${state.blocks} · Known headers: ${state.headers}`);
  const percentage = Number(state.verificationProgress) * 100;
  const valid = typeof state.verificationProgress === "number" && Number.isFinite(percentage) && percentage >= 0 && percentage <= 100;
  document.getElementById("verification").value = valid ? percentage : 0;
  text("verification-text", valid ? `Verification estimate: ${percentage.toFixed(2)}%${paused ? " · Paused" : ""}` : "Verification estimate unavailable");
}

async function request(path, body, authorized = false) {
  const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 12000);
  try {
    const headers = {"Content-Type": "application/json", "X-Qbit-Control": "1"};
    if (authorized && csrf) headers["X-Qbit-CSRF"] = csrf;
    const response = await fetch(path, {method: body ? "POST" : "GET", credentials: "same-origin", cache: "no-store", signal: abort.signal,
      ...(body ? {headers, body: JSON.stringify(body)} : {})});
    const value = await response.json();
    if (response.status === 401 && authorized) { controlReady = false; csrf = null; unlockButton.hidden = false; }
    if (!response.ok) throw new Error(value.error || "Request could not be confirmed. Refresh status before trying again.");
    return value;
  } finally { clearTimeout(timer); }
}

async function refresh() {
  try { state = await request("/api/status"); render(); }
  catch (error) {
    state = null; render();
    text("sync-detail", "Block progress unavailable");
    text("verification-text", "Verification estimate unavailable");
    document.getElementById("verification").value = 0;
    text("message", error.name === "AbortError" ? "Status request timed out. Try again." : error.message);
    for (const id of ["blocks", "headers", "connections", "txindex", "ready"]) text(id, "Unavailable");
  }
}

async function session() {
  try {
    const value = await request("/api/control/session");
    controlReady = value.unlocked === true; csrf = value.csrf;
    unlockButton.hidden = !value.available || controlReady;
    text("control-message", !value.available ? "Networking controls are unavailable in this installation." : controlReady ? "Changes are saved across app restarts. Turning networking off pauses syncing." : "Unlock with this app’s password from Umbrel to change networking. No terminal is needed.");
  } catch (_) { text("control-message", "Networking controls are unavailable. Status remains read-only."); }
  render();
}
unlockButton.addEventListener("click", () => {
  document.getElementById("control-login").hidden = false;
  document.getElementById("control-password").focus();
});
const loginButton = document.getElementById("control-login-button");
async function login() {
  if (controlPending) return;
  const input = document.getElementById("control-password"), password = input.value;
  input.value = ""; controlPending = true; loginButton.disabled = true;
  try {
    const value = await request("/api/control/login", {password});
    csrf = value.csrf; controlReady = true; unlockButton.hidden = true;
    document.getElementById("control-login").hidden = true;
    text("control-message", "Networking controls unlocked. Changes are saved across app restarts.");
  } catch (error) { text("control-message", error.name === "AbortError" ? "Unlock request timed out. Try again." : error.message); }
  finally { controlPending = false; loginButton.disabled = false; render(); }
}
loginButton.addEventListener("click", login);
document.getElementById("control-password").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); login(); } });
networkSwitch.addEventListener("change", async () => {
  const desired = networkSwitch.checked;
  if (!state || !controlReady || controlPending) { render(); return; }
  controlPending = true; render(); text("control-message", "Saving networking choice…");
  try {
    const value = await request("/api/control/network", {enabled: desired}, true);
    if (value.network !== state?.network || value.peerNetworkingEnabled !== desired) throw new Error("Networking state could not be confirmed. Refresh status.");
    state.peerNetworkingEnabled = value.peerNetworkingEnabled;
    text("control-message", desired ? "Networking is on. This choice is saved across restarts." : "Networking is off. Syncing is paused; downloaded blocks are preserved.");
  } catch (error) { text("control-message", error.name === "AbortError" ? "Change timed out and may still apply. Refresh status before trying again." : error.message); }
  finally { controlPending = false; render(); }
});
new DashboardRefresh(refresh);
session();
