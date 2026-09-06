(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root && root.document) api.mount(root.document);
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var VISUAL_FIELDS = [
    "primary_color", "secondary_color", "highlight_color", "background_color", "cjk_font", "latin_font",
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
  var PAGE_LABELS = {cover: "首页", toc: "目录页", section: "章节页", content: "正文", closing: "尾页", appendix: "附录"};
  function isAddedPage(page) { return String(page.composition_page_id || "").startsWith("structure:"); }
  function createState(templates, templateId, revision, taskbook, reason, confidence, composition) {
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
      recommendationConfidence: String(confidence || "low"),
      composition: composition ? copy(composition) : null,
      excludedPages: []
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
  function selectStructurePage(state, pageId, selected) {
    if (state.step !== 3) throw new Error("structure can only be changed in step 3");
    var page = state.composition && state.composition.pages.find(function (item) { return item.composition_page_id === pageId; });
    if (!page || !isAddedPage(page)) throw new Error("only added structure pages can be removed");
    var next = copy(state);
    next.excludedPages = next.excludedPages.filter(function (id) { return id !== pageId; });
    if (!selected) next.excludedPages.push(pageId);
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
    if (state.composition) {
      payload.confirmed_pages = state.composition.pages.filter(function (page) {
        return !isAddedPage(page) || state.excludedPages.indexOf(page.composition_page_id) === -1;
      }).map(function (page, index) {
        var record = copy(page);
        delete record.source_preview;
        record.output_page_number = index + 1;
        return record;
      });
      payload.structure_confirmed = true;
    }
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
    var structure = document.getElementById("structure");
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
    function renderStructure() {
      if (!structure || !state.composition) return;
      structure.hidden = false;
      var list = document.getElementById("structure-pages");
      var groups = document.getElementById("structure-groups");
      var checkboxes = [];
      var groupCheckboxes = [];
      list.replaceChildren();
      groups.replaceChildren();
      function selected(page) { return state.excludedPages.indexOf(page.composition_page_id) === -1; }
      function refresh() {
        var count = 0;
        checkboxes.forEach(function (row) {
          var keep = !isAddedPage(row.page) || selected(row.page);
          if (row.input) row.input.checked = keep;
          row.item.classList.toggle("excluded", !keep);
          row.number.textContent = keep ? "第 " + (++count) + " 页" : "不生成";
        });
        groupCheckboxes.forEach(function (group) {
          var kept = group.pages.filter(selected).length;
          group.input.checked = kept === group.pages.length;
          group.input.indeterminate = kept > 0 && kept < group.pages.length;
        });
        document.getElementById("structure-count").textContent = "确认后共 " + count + " 页";
      }
      ["cover", "toc", "section", "closing"].forEach(function (role) {
        var pages = state.composition.pages.filter(function (page) { return isAddedPage(page) && page.page_role === role; });
        if (!pages.length) return;
        var label = document.createElement("label");
        var input = document.createElement("input");
        input.type = "checkbox";
        input.addEventListener("change", function () {
          pages.forEach(function (page) { state = selectStructurePage(state, page.composition_page_id, input.checked); });
          refresh();
        });
        label.append(input, "新增" + PAGE_LABELS[role] + "（" + pages.length + "）");
        groups.append(label);
        groupCheckboxes.push({input: input, pages: pages});
      });
      state.composition.pages.forEach(function (page) {
        var item = document.createElement("li");
        var number = document.createElement("span");
        number.className = "structure-number";
        var label = document.createElement("label");
        var input = null;
        if (isAddedPage(page)) {
          input = document.createElement("input");
          input.type = "checkbox";
          input.addEventListener("change", function () {
            state = selectStructurePage(state, page.composition_page_id, input.checked);
            refresh();
          });
          label.append(input);
        }
        var title = document.createElement("strong");
        title.textContent = (PAGE_LABELS[page.page_role] || "页面") + " · " + (page.fixed_page_title || page.chapter_title || "无标题");
        label.append(title);
        var source = document.createElement("small");
        source.textContent = (isAddedPage(page) ? "新增页" : "原 Word 第 " + page.source_page_number + " 页（保留）") +
          (isAddedPage(page) && page.source_page_number ? " · 内容来源：Word 第 " + page.source_page_number + " 页" : "");
        var details = document.createElement("div");
        details.append(label, source);
        if (page.source_preview) {
          var preview = document.createElement("p");
          preview.className = "structure-preview";
          preview.textContent = page.source_preview;
          details.append(preview);
        }
        item.append(number, details);
        list.append(item);
        checkboxes.push({page: page, input: input, item: item, number: number});
      });
      var warnings = document.getElementById("structure-warnings");
      warnings.replaceChildren();
      (state.composition.warnings || []).forEach(function (warning) {
        var item = document.createElement("li");
        item.textContent = typeof warning === "string" ? warning : String(warning.message || warning.detail || warning.code || "请检查原文分页");
        warnings.append(item);
      });
      warnings.hidden = !warnings.children.length;
      refresh();
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
      if (state.step === 3) renderStructure();
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
        data.director_taskbook, data.recommendation_reason, data.recommendation_confidence, data.composition
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
    selectStructurePage: selectStructurePage,
    goNext: goNext,
    goBack: goBack,
    buildSubmission: buildSubmission,
    mount: mount
  };
}));
