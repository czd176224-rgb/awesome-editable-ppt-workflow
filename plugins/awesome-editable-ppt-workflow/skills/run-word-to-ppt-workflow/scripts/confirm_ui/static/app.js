(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root && root.document) api.mount(root.document);
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var VISUAL_FIELDS = [
    "primary_color", "secondary_color", "background_color", "cjk_font", "latin_font",
    "title_size_pt", "body_size_pt", "caption_size_pt"
  ];
  var TASKBOOK_FIELDS = [
    "use_scenario", "presenter", "primary_audience", "audience_prior_knowledge",
    "desired_outcome", "emphasis", "deemphasis"
  ];
  var NUMBER_FIELDS = new Set(["title_size_pt", "body_size_pt", "caption_size_pt"]);

  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  function templateById(templates, templateId) {
    return templates.find(function (item) { return item.id === templateId; }) || templates[0];
  }
  function exactFields(source, fields) {
    var values = {};
    fields.forEach(function (field) { values[field] = copy(source[field]); });
    return values;
  }
  function createState(templates, templateId, revision, taskbook, reason, confidence) {
    if (!Array.isArray(templates) || templates.length === 0) throw new Error("templates are required");
    var selected = templateById(templates, templateId);
    return {
      step: 1,
      templates: copy(templates),
      selectedTemplateId: selected.id,
      values: exactFields(selected.defaults, VISUAL_FIELDS),
      taskbook: exactFields(taskbook || selected.director_taskbook, TASKBOOK_FIELDS),
      baseRevision: revision || 0,
      recommendationReason: String(reason || ""),
      recommendationConfidence: String(confidence || "low")
    };
  }
  function applyTemplate(state, templateId) {
    if (state.step !== 1) throw new Error("templates can only be changed in step 1");
    var next = copy(state);
    var selected = templateById(next.templates, templateId);
    next.selectedTemplateId = selected.id;
    next.values = exactFields(selected.defaults, VISUAL_FIELDS);
    next.taskbook = exactFields(selected.director_taskbook, TASKBOOK_FIELDS);
    return next;
  }
  function updateField(state, field, value) {
    if (state.step !== 2) throw new Error("visual fields can only be changed in step 2");
    if (VISUAL_FIELDS.indexOf(field) === -1) throw new Error("unsupported visual field");
    var next = copy(state);
    next.values[field] = NUMBER_FIELDS.has(field) ? Number(value) : String(value);
    return next;
  }
  function updateTaskbook(state, field, value) {
    if (state.step !== 3) throw new Error("taskbook fields can only be changed in step 3");
    if (TASKBOOK_FIELDS.indexOf(field) === -1) throw new Error("unsupported taskbook field");
    var next = copy(state);
    next.taskbook[field] = String(value);
    return next;
  }
  function goNext(state) {
    var next = copy(state);
    if (next.step < 3) next.step += 1;
    return next;
  }
  function goBack(state) {
    var next = copy(state);
    if (next.step > 1) next.step -= 1;
    return next;
  }
  function buildSubmission(state, submissionId) {
    if (state.step !== 3) throw new Error("submission requires step 3");
    var payload = {
      submission_id: submissionId,
      revision: Number(state.baseRevision),
      selected_director_template_id: state.selectedTemplateId,
      director_taskbook: exactFields(state.taskbook, TASKBOOK_FIELDS)
    };
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
    var visualForm = document.getElementById("visual-form");
    var taskbookForm = document.getElementById("taskbook-form");
    var recommendation = document.getElementById("recommendation");
    var error = document.getElementById("error");
    var done = document.getElementById("done");
    var state = null;

    function showError(message) { error.textContent = message || ""; error.hidden = !message; }
    function fill(form, source, fields) {
      fields.forEach(function (field) { form.elements[field].value = source[field]; });
    }
    function readVisual() {
      VISUAL_FIELDS.forEach(function (field) { state = updateField(state, field, visualForm.elements[field].value); });
    }
    function readTaskbook() {
      TASKBOOK_FIELDS.forEach(function (field) { state = updateTaskbook(state, field, taskbookForm.elements[field].value); });
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
        var name = document.createElement("strong");
        name.textContent = template.name;
        var description = document.createElement("small");
        description.textContent = template.description;
        button.append(name, description);
        button.addEventListener("click", function () {
          state = applyTemplate(state, template.id);
          fill(visualForm, state.values, VISUAL_FIELDS);
          fill(taskbookForm, state.taskbook, TASKBOOK_FIELDS);
          render();
        });
        templatesNode.appendChild(button);
      });
    }
    function render() {
      panels.forEach(function (panel) { panel.hidden = Number(panel.dataset.step) !== state.step; });
      indicators.forEach(function (item) {
        var step = Number(item.dataset.stepTarget);
        item.classList.toggle("active", step === state.step);
        item.classList.toggle("complete", step < state.step);
      });
      recommendation.textContent = "系统推荐（" + state.recommendationConfidence + "）：" + state.recommendationReason;
      renderTemplates();
    }
    document.addEventListener("click", function (event) {
      var action = event.target.closest("[data-action]");
      if (!action || !state) return;
      showError("");
      try {
        if (action.dataset.action === "back") state = goBack(state);
        if (action.dataset.action === "next") {
          if (state.step === 2) {
            if (!visualForm.reportValidity()) return;
            readVisual();
          }
          state = goNext(state);
        }
        if (action.dataset.action === "submit") {
          if (!taskbookForm.reportValidity()) return;
          readTaskbook();
          action.disabled = true;
          requestJson("/api/confirm", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify(buildSubmission(state, submissionId()))
          }).then(function () {
            panels.forEach(function (panel) { panel.hidden = true; });
            done.hidden = false;
          }).catch(function (reason) {
            action.disabled = false;
            showError(reason.message);
          });
          return;
        }
        render();
      } catch (reason) { showError(reason.message); }
    });
    requestJson("/api/recommendations").then(function (data) {
      state = createState(
        data.templates, data.recommended_template_id, data.revision,
        data.director_taskbook, data.recommendation_reason, data.recommendation_confidence
      );
      fill(visualForm, state.values, VISUAL_FIELDS);
      fill(taskbookForm, state.taskbook, TASKBOOK_FIELDS);
      render();
    }).catch(function (reason) { showError(reason.message); });
  }
  return {
    VISUAL_FIELDS: VISUAL_FIELDS,
    TASKBOOK_FIELDS: TASKBOOK_FIELDS,
    createState: createState,
    applyTemplate: applyTemplate,
    updateField: updateField,
    updateTaskbook: updateTaskbook,
    goNext: goNext,
    goBack: goBack,
    buildSubmission: buildSubmission,
    mount: mount
  };
}));
