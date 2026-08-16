(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root && root.document) api.mount(root.document);
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var VISUAL_FIELDS = [
    "primary_color", "secondary_color", "background_color", "cjk_font", "latin_font",
    "title_size_pt", "body_size_pt", "caption_size_pt", "regional_characteristics",
    "visual_description"
  ];
  var NUMBER_FIELDS = new Set(["title_size_pt", "body_size_pt", "caption_size_pt"]);
  var LABELS = {
    primary_color: "主色", secondary_color: "辅助色", background_color: "背景色",
    cjk_font: "中文字体", latin_font: "西文字体", title_size_pt: "标题字号",
    body_size_pt: "正文字号", caption_size_pt: "图注字号",
    regional_characteristics: "地区特征", visual_description: "视觉描述"
  };

  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  function templateById(templates, templateId) {
    return templates.find(function (item) { return item.id === templateId; }) || templates[0];
  }
  function exactValues(defaults) {
    var values = {};
    VISUAL_FIELDS.forEach(function (field) { values[field] = copy(defaults[field]); });
    return values;
  }
  function createState(templates, templateId, revision) {
    if (!Array.isArray(templates) || templates.length === 0) throw new Error("templates are required");
    var selected = templateById(templates, templateId);
    return {step: 1, templates: copy(templates), selectedTemplateId: selected.id, values: exactValues(selected.defaults), baseRevision: revision || 0};
  }
  function applyTemplate(state, templateId) {
    if (state.step !== 1) throw new Error("templates can only be changed in step 1");
    var next = copy(state);
    var selected = templateById(next.templates, templateId);
    next.selectedTemplateId = selected.id;
    next.values = exactValues(selected.defaults);
    return next;
  }
  function isEditable(state) { return state.step === 2; }
  function updateField(state, field, value) {
    if (!isEditable(state)) throw new Error("review is read-only");
    if (VISUAL_FIELDS.indexOf(field) === -1) throw new Error("unsupported visual field");
    var next = copy(state);
    next.values[field] = NUMBER_FIELDS.has(field) ? Number(value) : String(value);
    return next;
  }
  function goNext(state) {
    if (state.step >= 3) return copy(state);
    var next = copy(state);
    next.step += 1;
    return next;
  }
  function goBack(state) {
    if (state.step <= 1) return copy(state);
    var next = copy(state);
    next.step -= 1;
    return next;
  }
  function buildSubmission(state, submissionId) {
    if (state.step !== 3) throw new Error("submission requires the read-only review step");
    var payload = {submission_id: submissionId, revision: Number(state.baseRevision) + 1};
    VISUAL_FIELDS.forEach(function (field) { payload[field] = copy(state.values[field]); });
    return payload;
  }

  function requestJson(url, options) {
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error(data.error || ("请求失败：" + response.status));
        return data;
      });
    });
  }
  function submissionId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") return globalThis.crypto.randomUUID();
    return "submission-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }
  function mount(document) {
    var panels = Array.from(document.querySelectorAll("[data-step]"));
    var indicators = Array.from(document.querySelectorAll("[data-step-target]"));
    var templatesNode = document.getElementById("templates");
    var form = document.getElementById("visual-form");
    var review = document.getElementById("review");
    var error = document.getElementById("error");
    var done = document.getElementById("done");
    var state = null;

    function showError(message) { error.textContent = message || ""; error.hidden = !message; }
    function readForm() {
      VISUAL_FIELDS.forEach(function (field) {
        state = updateField(state, field, form.elements[field].value);
      });
    }
    function fillForm() {
      VISUAL_FIELDS.forEach(function (field) { form.elements[field].value = state.values[field]; });
    }
    function renderTemplates() {
      templatesNode.replaceChildren();
      state.templates.forEach(function (template) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "template" + (template.id === state.selectedTemplateId ? " selected" : "");
        button.style.setProperty("--primary", template.defaults.primary_color);
        button.style.setProperty("--secondary", template.defaults.secondary_color);
        button.style.setProperty("--background", template.defaults.background_color);
        var title = document.createElement("strong"); title.textContent = template.name;
        var note = document.createElement("small"); note.textContent = template.description;
        button.append(title, note);
        button.addEventListener("click", function () { state = applyTemplate(state, template.id); fillForm(); render(); });
        templatesNode.appendChild(button);
      });
    }
    function renderReview() {
      review.replaceChildren();
      VISUAL_FIELDS.forEach(function (field) {
        var row = document.createElement("div");
        var key = document.createElement("dt"); key.textContent = LABELS[field];
        var value = document.createElement("dd"); value.textContent = state.values[field] || "未指定";
        row.append(key, value); review.appendChild(row);
      });
    }
    function render() {
      panels.forEach(function (panel) { panel.hidden = Number(panel.dataset.step) !== state.step; });
      indicators.forEach(function (button) {
        var target = Number(button.dataset.stepTarget);
        button.classList.toggle("active", target === state.step);
        button.classList.toggle("complete", target < state.step);
      });
      renderTemplates();
      if (state.step === 2) fillForm();
      if (state.step === 3) renderReview();
    }
    document.addEventListener("click", function (event) {
      var button = event.target.closest("[data-action]");
      if (!button || !state) return;
      showError("");
      try {
        if (button.dataset.action === "back") state = goBack(state);
        if (button.dataset.action === "next") {
          if (state.step === 2) { if (!form.reportValidity()) return; readForm(); }
          state = goNext(state);
        }
        if (button.dataset.action === "submit") {
          button.disabled = true;
          requestJson("/api/confirm", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(buildSubmission(state, submissionId()))})
            .then(function () { panels.forEach(function (panel) { panel.hidden = true; }); done.hidden = false; })
            .catch(function (failure) { button.disabled = false; showError(failure.message); });
          return;
        }
        render();
      } catch (failure) { showError(failure.message); }
    });
    requestJson("/api/recommendations").then(function (data) {
      state = createState(data.templates, data.recommended_template_id, data.revision);
      fillForm(); render();
    }).catch(function (failure) { showError(failure.message); });
  }

  return {
    VISUAL_FIELDS: VISUAL_FIELDS.slice(), createState: createState, applyTemplate: applyTemplate,
    updateField: updateField, isEditable: isEditable, goNext: goNext, goBack: goBack,
    buildSubmission: buildSubmission, mount: mount
  };
}));
