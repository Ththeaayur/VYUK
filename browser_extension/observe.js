// VYUK Browser Bridge — page observer (click-anywhere Batch 1, Plan 9 §1.2).
//
// Own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).
//
// Injected via chrome.scripting.executeScript ON DEMAND from the
// service worker — NOT a content script (there are no manifest
// content scripts in v1). Runs once per observation and returns one
// observation record.
//
// - Mints ONE pass id per observation (taken from
//   window.__vyukPassId when the worker set one, else minted here)
//   and carries it on every read in the record.
// - Reads the DOM box (getBoundingClientRect() centre;
//   Range.getBoundingClientRect() for text-node sub-boxes;
//   document.elementFromPoint check; computed transform matrix),
//   window.devicePixelRatio, document.documentElement.clientWidth /
//   clientHeight, innerWidth / innerHeight.
// - Per-frame walk: each frame's origin is checked BEFORE any read
//   of that frame. A denied-origin frame is refused BY NAME
//   ({origin, denied: true, reason}) — never silently skipped.
//   Cross-origin frames are recorded {origin: 'cross-origin',
//   nested: 'unwalkable'}.
// - Snapshot exclusion (M-8): no input / textarea / contenteditable
//   / select VALUES appear in observations — only the control's
//   role/name/box, never what was typed into it.
// - Per-element text cap 1 KB (M-13): over-long text is truncated
//   with a "truncated" flag, never sent whole.

(function () {
  'use strict';

  var ELEMENT_TEXT_CAP = 1024;

  function mintPassId() {
    var bytes = new Uint8Array(8);
    if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
      crypto.getRandomValues(bytes);
    } else {
      for (var i = 0; i < bytes.length; i++) {
        bytes[i] = Math.floor(Math.random() * 256);
      }
    }
    var out = '';
    for (var j = 0; j < bytes.length; j++) {
      out += ('0' + bytes[j].toString(16)).slice(-2);
    }
    return 'obs-' + Date.now().toString(36) + '-' + out;
  }

  var passId = window.__vyukPassId || mintPassId();
  var deniedOrigins = window.__vyukDeniedOrigins || [];

  function originDenied(origin) {
    if (!origin || origin === 'null') {
      return false;
    }
    var host = origin;
    try {
      host = new URL(origin).hostname.toLowerCase();
    } catch (err) {
      host = String(origin).toLowerCase();
    }
    for (var i = 0; i < deniedOrigins.length; i++) {
      var domain = String(deniedOrigins[i]).toLowerCase().replace(/\.$/, '');
      if (host === domain || host.slice(-(domain.length + 1)) === '.' + domain) {
        return true;
      }
    }
    return false;
  }

  function truncateText(text) {
    var value = text == null ? '' : String(text);
    if (value.length <= ELEMENT_TEXT_CAP) {
      return { text: value, truncated: false };
    }
    return { text: value.slice(0, ELEMENT_TEXT_CAP), truncated: true };
  }

  function isExcluded(el) {
    if (!el || !el.tagName) {
      return false;
    }
    var tag = el.tagName.toUpperCase();
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
      return true;
    }
    return el.isContentEditable === true;
  }

  function elementBox(doc, el) {
    var rect = el.getBoundingClientRect();
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;
    var atPoint = null;
    try {
      var hit = doc.elementFromPoint(cx, cy);
      atPoint = hit === el || (hit !== null && el.contains(hit));
    } catch (err) {
      atPoint = null;
    }
    var transform = '';
    try {
      transform = window.getComputedStyle(el).transform || '';
    } catch (err) {
      transform = '';
    }
    return {
      x: rect.left, y: rect.top, width: rect.width, height: rect.height,
      centre_x: cx, centre_y: cy, at_point: atPoint, transform: transform,
    };
  }

  function textSubBoxes(el) {
    // Text-node sub-boxes: one box per text node, so a click can land
    // on the words rather than the element's padded box.
    var boxes = [];
    var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      if (!node.textContent || !node.textContent.trim()) {
        continue;
      }
      try {
        var range = document.createRange();
        range.selectNodeContents(node);
        var rects = range.getClientRects();
        for (var i = 0; i < rects.length; i++) {
          var r = rects[i];
          boxes.push({
            x: r.left, y: r.top, width: r.width, height: r.height,
            text: truncateText(node.textContent.trim()).text,
          });
        }
        range.detach();
      } catch (err) {
        // A detached node mid-walk: skip that sub-box, keep the rest.
      }
    }
    return boxes;
  }

  function collectElements(doc) {
    var out = [];
    var candidates = doc.querySelectorAll(
      'a, button, input, textarea, select, [role="button"], [role="link"], ' +
      '[role="textbox"], [role="checkbox"], [role="radio"], [role="menuitem"], ' +
      '[onclick], summary, details');
    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      var excluded = isExcluded(el);
      var labelled = truncateText(
        el.getAttribute('aria-label') ||
        el.getAttribute('title') ||
        (el.innerText || '').trim().slice(0, 200));
      out.push({
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || el.tagName.toLowerCase(),
        name: labelled.text,
        name_truncated: labelled.truncated,
        // Snapshot exclusion: values of inputs / textareas /
        // contenteditables / selects are NEVER read.
        value: excluded ? null : undefined,
        value_excluded: excluded,
        box: elementBox(doc, el),
        text_boxes: excluded ? [] : textSubBoxes(el),
      });
    }
    return out;
  }

  function walkFrame(win, depth) {
    var origin;
    try {
      origin = win.location.origin;
    } catch (err) {
      // Cross-origin: the origin itself is unreadable — record the
      // frame as unwalkable, never silently skip it.
      return { origin: 'cross-origin', nested: 'unwalkable', depth: depth };
    }
    if (originDenied(origin)) {
      // Denied-origin frame: refused BY NAME before any read.
      return {
        origin: origin, denied: true, depth: depth,
        reason: 'denylist — refused, content not read',
      };
    }
    var doc;
    try {
      doc = win.document;
    } catch (err) {
      return { origin: origin, nested: 'unwalkable', depth: depth };
    }
    var frames = [];
    var childCount = 0;
    try {
      childCount = win.frames.length;
    } catch (err) {
      childCount = 0;
    }
    for (var i = 0; i < childCount; i++) {
      try {
        frames.push(walkFrame(win.frames[i], depth + 1));
      } catch (err) {
        frames.push({ origin: 'cross-origin', nested: 'unwalkable', depth: depth + 1 });
      }
    }
    return {
      origin: origin,
      depth: depth,
      devicePixelRatio: win.devicePixelRatio,
      clientWidth: doc.documentElement.clientWidth,
      clientHeight: doc.documentElement.clientHeight,
      innerWidth: win.innerWidth,
      innerHeight: win.innerHeight,
      url: (function () {
        try { return win.location.href; } catch (err) { return ''; }
      })(),
      elements: collectElements(doc),
      frames: frames,
    };
  }

  var root = walkFrame(window, 0);
  return {
    v: 1,
    kind: 'observation',
    observation_pass_id: passId,
    devicePixelRatio: window.devicePixelRatio,
    clientWidth: document.documentElement.clientWidth,
    clientHeight: document.documentElement.clientHeight,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    url: window.location.href,
    frames: [root],
  };
})();
