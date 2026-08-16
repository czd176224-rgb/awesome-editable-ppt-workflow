(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ColorTools = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function channel(value) {
    var parsed = Number(value);
    if (!Number.isFinite(parsed)) throw new Error("RGB channels must be numbers");
    return Math.max(0, Math.min(255, Math.round(parsed)));
  }

  function normalizeHex(value) {
    var text = String(value || "").trim();
    if (/^#[0-9a-f]{3}$/i.test(text)) {
      text = "#" + text.slice(1).split("").map(function (item) { return item + item; }).join("");
    }
    if (!/^#[0-9a-f]{6}$/i.test(text)) throw new Error("Color must be a six-digit HEX value");
    return text.toUpperCase();
  }

  function hexToRgb(value) {
    var hex = normalizeHex(value).slice(1);
    return {
      r: parseInt(hex.slice(0, 2), 16),
      g: parseInt(hex.slice(2, 4), 16),
      b: parseInt(hex.slice(4, 6), 16)
    };
  }

  function rgbToHex(value) {
    return "#" + [value.r, value.g, value.b].map(function (item) {
      return channel(item).toString(16).padStart(2, "0");
    }).join("").toUpperCase();
  }

  function createDraft(value) {
    var normalized = normalizeHex(value);
    return {committed: normalized, preview: normalized};
  }

  function setDraftHex(draft, value) {
    draft.preview = normalizeHex(value);
    return draft.preview;
  }

  function setDraftRgb(draft, value) {
    draft.preview = rgbToHex(value);
    return draft.preview;
  }

  function commitDraft(draft) {
    draft.committed = draft.preview;
    return draft.committed;
  }

  function cancelDraft(draft) {
    draft.preview = draft.committed;
    return draft.preview;
  }

  return {
    normalizeHex: normalizeHex,
    hexToRgb: hexToRgb,
    rgbToHex: rgbToHex,
    createDraft: createDraft,
    setDraftHex: setDraftHex,
    setDraftRgb: setDraftRgb,
    commitDraft: commitDraft,
    cancelDraft: cancelDraft
  };
});
