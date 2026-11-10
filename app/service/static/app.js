"use strict";
const text = (id, value) => { document.getElementById(id).textContent = String(value ?? "—"); };
const networkSwitch = document.getElementById("network-active");
let state = null, controlPending = false;

function render() {
  networkSwitch.disabled = !state?.networkControlAvailable || controlPending;
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

async function request(path, body) {
  const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 12000);
  try {
    const headers = {"Content-Type": "application/json", "X-Qbit-Control": "1"};
    const response = await fetch(path, {method: body ? "POST" : "GET", credentials: "same-origin", cache: "no-store", signal: abort.signal,
      ...(body ? {headers, body: JSON.stringify(body)} : {})});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || "Request could not be confirmed. Refresh status before trying again.");
    return value;
  } finally { clearTimeout(timer); }
}

async function refresh() {
  try {
    const controlWasAvailable = state?.networkControlAvailable;
    state = await request("/api/status");
    if (!controlPending && state.networkControlAvailable !== controlWasAvailable) {
      text("control-message", state.networkControlAvailable ? "" : "Open Qbit Core through Umbrel to change networking.");
    }
    render();
  }
  catch (error) {
    state = null; render();
    text("sync-detail", "Block progress unavailable");
    text("verification-text", "Verification estimate unavailable");
    document.getElementById("verification").value = 0;
    text("message", error.name === "AbortError" ? "Status request timed out. Try again." : error.message);
    for (const id of ["blocks", "headers", "connections", "txindex", "ready"]) text(id, "Unavailable");
  }
}

networkSwitch.addEventListener("change", async () => {
  const desired = networkSwitch.checked;
  if (!state?.networkControlAvailable || controlPending) { render(); return; }
  controlPending = true; render(); text("control-message", "Saving networking choice…");
  try {
    const value = await request("/api/control/network", {enabled: desired});
    if (value.network !== state?.network || value.peerNetworkingEnabled !== desired) throw new Error("Networking state could not be confirmed. Refresh status.");
    state.peerNetworkingEnabled = value.peerNetworkingEnabled;
    text("control-message", desired ? "Networking is on. This choice is saved across restarts." : "Networking is off. Syncing is paused; downloaded blocks are preserved.");
  } catch (error) { text("control-message", error.name === "AbortError" ? "Change timed out and may still apply. Refresh status before trying again." : error.message); }
  finally { controlPending = false; render(); }
});
new DashboardRefresh(refresh);
