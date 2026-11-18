// VYUK Browser Bridge — service worker (click-anywhere Batch 1, Plan 9 §1.2).
//
// Own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).
//
// MV3 reconnect + the wake (M-7): a service worker terminates after
// ~30 s idle; setTimeout/setInterval die with it and are NEVER used
// for anything that must outlive 30 s. The wake is chrome.alarms
// (1-minute period), created at goal start and cleared at goal end
// (never 24/7). On each firing the worker checks the native port and
// reconnects via connectNative if dead, re-handshakes, and
// re-establishes state from VYUK's main process (state lives there;
// the host is stateless; this worker holds only a cache rebuilt on
// handshake).
//
// The channel token NEVER leaves the host: this worker does not call
// the localhost channel directly. It asks the host over native
// messaging ({kind: "goal-status"}), and the host GETs /goal and
// applies the orphaned-alarm clearer (N-19, R-7).
//
// Reconnect latency: after a confirm resolves, VYUK tolerates up to
// one alarm period (60 s), then retries the command once; still dead
// -> the goal pauses with "extension link lost".
//
// Orphaned alarm (N-19, R-7): on each firing the worker asks the host
// for the goal status; when there is no live goal, or a paused goal
// paused > 30 min, it clears its own alarm (chrome.alarms.clear) and
// disconnects. The goal record stays paused — expiry clears the
// alarm, never the goal.

'use strict';

const HOST_NAME = 'com.vyuk.browser_host';
const ALARM_NAME = 'vyuk-browser-wake';
const ALARM_PERIOD_MINUTES = 1;

let port = null;          // chrome.runtime.Port | null
let cache = null;         // rebuilt from the handshake on every reconnect
let goalActive = false;

function log(...args) {
  console.log('[vyuk-bridge]', ...args);
}

function connectNative() {
  if (port) {
    return port;
  }
  try {
    port = chrome.runtime.connectNative(HOST_NAME);
  } catch (err) {
    port = null;
    log('connectNative failed:', err && err.message ? err.message : err);
    return null;
  }
  port.onMessage.addListener(onHostMessage);
  port.onDisconnect.addListener(() => {
    log('native port disconnected:',
      chrome.runtime.lastError ? chrome.runtime.lastError.message : '(no error)');
    port = null;
    cache = null;
  });
  // Handshake: the host re-handshakes over the channel and returns
  // the live goal record; the cache is rebuilt from the response.
  port.postMessage({ v: 1, kind: 'hello' });
  return port;
}

function ensurePort() {
  if (port) {
    return port;
  }
  return connectNative();
}

function onHostMessage(message) {
  if (!message || message.v !== 1) {
    return;
  }
  if (message.kind === 'hello') {
    cache = { hostId: message.host_id, dpiAwareness: message.dpi_awareness };
    log('handshake ok, dpi:', cache.dpiAwareness);
    return;
  }
  if (message.kind === 'goal-status') {
    cache = cache || {};
    cache.goal = message.goal || null;
    cache.latestPassId = message.latest_pass_id || null;
    if (message.alarm === 'clear') {
      clearWake('no live goal (or paused > 30 min) — alarm cleared');
    }
    return;
  }
  if (message.kind === 'rotation') {
    // The host rotated (VYUK restarted mid-goal): the goal pauses
    // and re-gates on resume. This worker does not re-dispatch.
    log('host rotated, pause-and-re-gate:', message.status);
    cache = cache || {};
    cache.rotated = message.status;
    return;
  }
  if (message.kind === 'observation-ack') {
    cache = cache || {};
    cache.latestPassId = message.latest_pass_id || null;
    return;
  }
  if (message.kind === 'error') {
    log('host error:', message.error);
    return;
  }
  // Command kinds (observe, ...) arrive as native messages from the
  // host; dispatch them to the page via scripting (see dispatch()).
  if (message.kind === 'observe' || message.kind === 'command') {
    dispatch(message);
  }
}

async function dispatch(command) {
  const target = command.target || {};
  if (!target.tabId) {
    log('observe command without a tabId — refusing');
    return;
  }
  // The pass id and the denylist ride in as page globals before the
  // observation runs, so one pass id covers every frame read and a
  // denied-origin frame is refused before any read of it. The
  // denylist comes from the host (which reads the store) in the
  // command; the worker never reads the file itself.
  const deniedOrigins = command.denied_origins || [];
  const passId = command.observation_pass_id || null;
  try {
    await chrome.scripting.executeScript({
      target: { tabId: target.tabId, allFrames: true },
      func: (origins, pid) => {
        window.__vyukDeniedOrigins = origins;
        if (pid) {
          window.__vyukPassId = pid;
        }
      },
      args: [deniedOrigins, passId],
    });
    const results = await chrome.scripting.executeScript({
      target: { tabId: target.tabId, allFrames: true },
      files: ['observe.js'],
    });
    const observations = (results || []).map((r) => r.result).filter(Boolean);
    const p = ensurePort();
    if (p) {
      p.postMessage({
        v: 1,
        kind: 'observation',
        observation_pass_id: command.observation_pass_id || null,
        observations,
      });
    }
  } catch (err) {
    log('executeScript failed:', err && err.message ? err.message : err);
    const p = ensurePort();
    if (p) {
      p.postMessage({
        v: 1,
        kind: 'observation',
        observation_pass_id: command.observation_pass_id || null,
        error: err && err.message ? err.message : String(err),
      });
    }
  }
}

function createWake() {
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: ALARM_PERIOD_MINUTES });
  log('wake alarm created (1 min, goal-scoped)');
}

function clearWake(reason) {
  chrome.alarms.clear(ALARM_NAME, (wasCleared) => {
    log('wake alarm cleared:', reason, 'wasCleared =', wasCleared);
  });
  goalActive = false;
  if (port) {
    try { port.disconnect(); } catch (err) { /* already dead */ }
    port = null;
  }
  cache = null;
}

// Goal lifecycle: VYUK's main process tells the worker (via the host)
// when a browser goal starts and ends.
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.v !== 1) {
    return false;
  }
  if (message.kind === 'goal-start') {
    goalActive = true;
    createWake();
    ensurePort();
    sendResponse({ v: 1, ok: true });
    return true;
  }
  if (message.kind === 'goal-end') {
    clearWake('goal ended');
    sendResponse({ v: 1, ok: true });
    return true;
  }
  return false;
});

// The wake: on each firing, check the port, reconnect if dead,
// re-handshake, and run the orphaned-alarm clearer via the host.
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== ALARM_NAME) {
    return;
  }
  if (!goalActive) {
    // No goal is running in this worker — nothing to wake for.
    clearWake('alarm fired with no active goal');
    return;
  }
  const p = ensurePort();
  if (!p) {
    log('wake: native port still dead after reconnect attempt');
    return;
  }
  // Ask the host for the live goal; the host applies the clearer
  // and this worker clears its own alarm when told to.
  p.postMessage({ v: 1, kind: 'goal-status' });
});

// Reconnect on startup (e.g. after a browser restart mid-goal): the
// alarm is re-created only when a goal is live — the host's
// goal-status reply says so, and a dead goal clears the alarm.
chrome.runtime.onStartup.addListener(() => {
  const p = ensurePort();
  if (p) {
    p.postMessage({ v: 1, kind: 'goal-status' });
  }
});
