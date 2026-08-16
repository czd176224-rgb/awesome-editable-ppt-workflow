(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.VisualSystem = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function number(value, fallback) {
    var parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function palette(state) {
    return (state.color && state.color.palette) || {};
  }

  function fontStack(stack) {
    stack = stack || {};
    return [stack.cjk, stack.latin, stack.css].filter(Boolean).map(function (item) {
      return String(item).indexOf(" ") >= 0 ? '"' + item + '"' : item;
    }).join(", ");
  }

  function firstLayout(state) {
    var layouts = Array.isArray(state.layout_preferences) ? state.layout_preferences : [];
    var explicit = layouts.find(function (item) { return item !== "auto"; });
    return explicit || "auto";
  }

  function derivePreview(state) {
    state = state || {};
    var colors = palette(state);
    var typography = state.typography || {};
    var scale = typography.type_scale_pt || {};
    var axes = state.style_axes || {};
    var density = state.information_density || "balanced";
    var densityMap = {
      low: {gap: "22px", padding: "28px", line: "1.65"},
      balanced: {gap: "16px", padding: "22px", line: "1.48"},
      high: {gap: "10px", padding: "16px", line: "1.32"}
    };
    var densityTokens = densityMap[density] || densityMap.balanced;
    var formal = number(axes.formal, 50);
    var modern = number(axes.modern, 50);
    var minimal = number(axes.minimal, 50);
    var classes = ["style-" + (state.visual_style || "editorial"), "density-" + density];
    if (formal >= 65) classes.push("is-formal");
    if (modern >= 30) classes.push("is-modern");
    if (minimal >= 65) classes.push("is-minimal");
    return {
      layout: firstLayout(state),
      classes: classes,
      iconFamily: state.icons || "auto-by-logic",
      imageTreatment: (state.image_rendering && state.image_rendering.rendering) || "auto",
      vars: {
        "--preview-bg": colors.background || "#FFFFFF",
        "--preview-secondary": colors.secondary_bg || "#F2F4F7",
        "--preview-primary": colors.primary || "#17365D",
        "--preview-accent": colors.accent || "#D97706",
        "--preview-secondary-accent": colors.secondary_accent || "#4B74A6",
        "--preview-body": colors.body_text || "#1F2937",
        "--preview-section": colors.section_title || colors.primary || "#17365D",
        "--preview-number": colors.key_number || colors.accent || "#D97706",
        "--preview-table-header": colors.table_header || colors.primary || "#17365D",
        "--preview-border": colors.border || "#CBD5E1",
        "--preview-heading-font": fontStack(typography.heading) || "sans-serif",
        "--preview-body-font": fontStack(typography.body) || "sans-serif",
        "--preview-title-size": number(scale.page_title, 28) + "pt",
        "--preview-section-size": number(scale.section_title, 18) + "pt",
        "--preview-body-size": number(scale.body, 12) + "pt",
        "--preview-caption-size": number(scale.caption, 9) + "pt",
        "--preview-card-gap": densityTokens.gap,
        "--preview-card-padding": densityTokens.padding,
        "--preview-line-height": densityTokens.line,
        "--preview-radius": Math.round((100 - formal + modern) * 0.1) + "px",
        "--preview-shadow-alpha": String(Math.max(0, Math.min(0.18, (100 - minimal) / 550)))
      }
    };
  }

  function applyPreview(element, preview) {
    Object.keys(preview.vars).forEach(function (name) {
      element.style.setProperty(name, preview.vars[name]);
    });
    element.dataset.layout = preview.layout;
    element.dataset.iconFamily = preview.iconFamily;
    element.dataset.imageTreatment = preview.imageTreatment;
    element.className = "slide-canvas " + preview.classes.join(" ");
  }

  function specimenText(state, field) {
    var density = state.information_density || "balanced";
    var layout = firstLayout(state);
    var rendering = (state.image_rendering && state.image_rendering.rendering) || "auto";
    var map = {
      color: ["语义色彩", "标题、正文、强调与背景使用各自固定角色"],
      typography: ["三级文字层级", "页面标题、分组标题与正文按真实字号关系展示"],
      information_density: ["信息密度 · " + density, density === "high" ? "减少留白并提高单位页面承载量" : density === "low" ? "增加留白并强化单一重点" : "在阅读速度与内容承载之间保持均衡"],
      layout_preferences: ["页面组织 · " + layout, "这是优先组织语言，不会把所有页面锁成同一版式"],
      image_rendering: ["图像语言 · " + rendering, "只限定图像气质，具体画面仍由每页内容决定"],
      image_role: ["图像角色", "改变图片承担证据、叙事或解释任务时的面积和位置"],
      evidence_strength: ["证据表达", "改变结论、数据、案例和附件证据之间的视觉权重"],
      composition_tendency: ["构图倾向", "改变页面重心和阅读路径，不固定具体布局"],
      background_system: ["背景体系", "控制整套页面的明暗节奏与反差"],
      brand_device: ["品牌装置", "控制页眉、线条、页脚和纹理的存在感"]
    };
    return map[field] || ["当前视觉规则", "该选择会被写入风格合同并作用于所有页面"];
  }

  function deriveSpecimen(state, field) {
    var preview = derivePreview(state);
    var kinds = {color: "color", typography: "typography", information_density: "density"};
    var copy = specimenText(state || {}, field);
    return {kind: kinds[field] || "layout", caption: copy[0], effect: copy[1], vars: preview.vars, classes: preview.classes};
  }

  return {derivePreview: derivePreview, deriveSpecimen: deriveSpecimen, applyPreview: applyPreview};
});
