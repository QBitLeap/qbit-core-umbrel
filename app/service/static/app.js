"use strict";
let pending = false;
const button = document.getElementById("refresh");
const text = (id, value) => { document.getElementById(id).textContent = String(value ?? "—"); };
async function refresh() {
  if (pending || document.hidden) return;
  pending = true; button.disabled = true;
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 12000);
  try {
    const response = await fetch("/api/status", { cache: "no-store", signal: abort.signal });
    const value = await response.json();
    if (!response.ok) throw new Error("Node status is unavailable. Try again shortly.");
    for (const id of ["network", "blocks", "headers", "connections"]) text(id, value[id]);
    text("txindex", value.txindexSynced ? "Synchronized" : "Catching up");
    text("ready", value.chainAndIndexReady ? "Synchronized" : "Catching up");
    text("message", value.peerNetworkingEnabled ? (value.chainAndIndexReady ? "Your node is synchronized." : "Your node is synchronizing.") : "Your node is running with peer networking off.");
    document.getElementById("setup").hidden = value.peerNetworkingEnabled;
  } catch (error) {
    text("message", error.name === "AbortError" ? "Status request timed out. Try again." : error.message);
    for (const id of ["blocks", "headers", "connections", "txindex", "ready"]) text(id, "Unavailable");
  } finally { clearTimeout(timer); pending = false; button.disabled = false; }
}
button.addEventListener("click", refresh);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
setInterval(refresh, 15000);
refresh();
