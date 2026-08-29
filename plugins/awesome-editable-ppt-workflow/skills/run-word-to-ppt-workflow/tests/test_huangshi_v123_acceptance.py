from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt


TESTS = Path(__file__).resolve().parent
PLUGIN = TESTS.parents[2]
REPO = TESTS.parents[4]
SCRIPTS = PLUGIN / "skills/run-word-to-ppt-workflow/scripts"
sys.path.insert(0, str(SCRIPTS))

from extract_docx_pages import DEFAULT_MARKER, extract  # noqa: E402


DESKTOP = Path.home() / "Desktop"
WORD = DESKTOP / "黄石市产业创新与母基金专业化管理合作建议_PPT生成专用Word副本_V3.docx"
LOGO = DESKTOP / "尚融logo.png"
WORD_SHA256 = "519FC2C5DAA0B4A2E65954E6FA20DF461E04587749C69AFB5952C6535A4A4A11"
LOGO_SHA256 = "9681840BACFBA51E87E47D687C1CA1F9C542F9C235577280447E96070726BCF0"
SELECTED = (5, 10, 14, 20, 21, 40)
OUTPUT = REPO / "tmp/v1.2.3-acceptance/huangshi"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _page_text(page: dict) -> str:
    values: list[str] = []
    for block in page["blocks"]:
        if block["type"] == "table":
            values.extend(cell for row in block["rows"] for cell in row)
        else:
            values.append(block.get("text", ""))
    return "\n".join(values)


def _add_text(slide, name: str, text: str, x: float, y: float, w: float, h: float, *, size: int = 15, bold: bool = False):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    shape.name = name
    shape.text_frame.clear()
    paragraph = shape.text_frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.CENTER
    run = paragraph.add_run()
    run.text = text
    run.font.name = "Microsoft YaHei"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor(35, 45, 60)
    return shape


def _add_node(slide, name: str, text: str, x: float, y: float, w: float, h: float, *, fill: str = "EAF2F8"):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.name = name
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(fill)
    shape.line.color.rgb = RGBColor.from_string("6B7A90")
    shape.text = text
    for paragraph in shape.text_frame.paragraphs:
        paragraph.alignment = PP_ALIGN.CENTER
        for run in paragraph.runs:
            run.font.name = "Microsoft YaHei"
            run.font.size = Pt(13)
            run.font.color.rgb = RGBColor.from_string("233044")
    return shape


def _title(slide, page: int, text: str) -> None:
    _add_text(slide, f"page-{page}-title", text, 0.55, 0.25, 12.2, 0.6, size=21, bold=True)


def _build_deck(source: dict, output: Path) -> None:
    deck = Presentation()
    deck.slide_width = Inches(13.333333)
    deck.slide_height = Inches(7.5)
    pages = {page["page_number"]: page for page in source["pages"]}

    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _title(slide, 5, "四个离岸科创中心已形成资源入口，母基金要接续完成产业转化")
    for index, text in enumerate(("科技成果\n40余项", "活动\n35场", "项目和人才团队\n30余个", "注册落地项目\n7个")):
        _add_node(slide, f"page-5-independent-kpi-{index + 1}", text, 0.65 + index * 3.13, 2.35, 2.65, 1.45)

    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _title(slide, 10, "“十五五”量化目标为基金投向和绩效评价提供明确坐标")
    _add_text(slide, "page-10-economic-group", "经济目标", 0.55, 1.15, 12.2, 0.35, bold=True)
    economic = ("数字经济核心产业增加值\n420亿元", "通用人工智能产业\n100亿元")
    for index, text in enumerate(economic):
        _add_node(slide, f"page-10-independent-kpi-{index + 1}", text, 1.7 + index * 5.2, 1.6, 4.7, 1.0)
    _add_text(slide, "page-10-implementation-group", "实施目标", 0.55, 3.0, 12.2, 0.35, bold=True)
    implementation = ("450家企业\n数字化改造", "100个以上\n省级AI应用示范场景", "50家\n省级以上绿色工厂", "新增3—5家\n上市企业")
    for index, text in enumerate(implementation):
        _add_node(slide, f"page-10-independent-kpi-{index + 3}", text, 0.55 + index * 3.18, 3.5, 2.7, 1.3)

    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _title(slide, 14, "从外部创新资源到黄石产业增量，需要打通七个连续转化环节")
    stages = ("项目发现", "技术评价", "商业验证", "中试放大", "落地承接", "成长赋能", "并购退出")
    for index, text in enumerate(stages):
        _add_node(slide, f"page-14-stage-{index + 1}", text, 0.35 + index * 1.83, 2.4, 1.55, 1.35)

    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _title(slide, 20, "现有“1+4+N”已覆盖企业全生命周期，可承载新增专业工具")
    _add_node(slide, "page-20-node-root", "1\n产业发展母基金", 5.4, 1.2, 2.5, 0.85, fill="D6EAF8")
    for index, text in enumerate(("天使类", "科创类", "产业类", "专项类")):
        _add_node(slide, f"page-20-node-group-{index + 1}", text, 0.8 + index * 3.15, 2.75, 2.5, 0.85)
    for index, text in enumerate(("细分子基金A", "细分子基金B", "细分子基金N")):
        _add_node(slide, f"page-20-node-n-{index + 1}", text, 2.3 + index * 3.2, 4.45, 2.5, 0.85)

    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _title(slide, 21, "黄石基金已进入落地阶段，公开数据尚不足以判断运营成熟度")
    _add_node(slide, "page-21-disclosed-fact-1", "基金合作\n总规模百亿元", 1.1, 1.55, 4.9, 1.25)
    _add_node(slide, "page-21-disclosed-fact-2", "招商项目签约\n总投资68.2亿元", 7.3, 1.55, 4.9, 1.25)
    _add_node(slide, "page-21-data-gap-warning", "数据缺口：未完整披露母基金实缴、子基金设立和实缴比例、组合收益、项目清单、投后成效及退出回款；不能据此做跨口径比较或判断运营已完全成熟。", 1.1, 3.4, 11.1, 1.45, fill="FFF2CC")

    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _title(slide, 40, "前90天夯实管理基础，12个月形成子基金、项目、赋能与退出的可验证成果")
    labels = ("0—30天", "31—60天", "61—90天")
    for index, text in enumerate(labels):
        _add_node(slide, f"page-40-segment-{index + 1}", text, 0.9 + index * 3.85, 2.05, 3.45, 1.0, fill=("D6EAF8", "D5F5E3", "FCF3CF")[index])
    _add_node(slide, "page-40-twelve-month-milestone", "12个月成果里程碑（独立于前90天任务条）", 4.3, 4.05, 4.75, 1.0, fill="E8DAEF")

    assert all(_page_text(pages[number]) for number in SELECTED)
    output.parent.mkdir(parents=True, exist_ok=True)
    deck.save(output)


def _render_previews(deck_path: Path, output: Path) -> None:
    deck = Presentation(deck_path)
    output.mkdir(parents=True, exist_ok=True)
    font_path = Path(r"C:\Windows\Fonts\msyh.ttc")
    body_font = ImageFont.truetype(str(font_path), 20) if font_path.is_file() else ImageFont.load_default()
    title_font = ImageFont.truetype(str(font_path), 28) if font_path.is_file() else body_font
    for page_number, slide in zip(SELECTED, deck.slides):
        canvas = Image.new("RGB", (1600, 900), "white")
        draw = ImageDraw.Draw(canvas)
        draw.text((60, 45), next(shape.text for shape in slide.shapes if shape.name.endswith("title")), fill="#172033", font=title_font)
        for shape in slide.shapes:
            if not shape.has_text_frame or shape.name.endswith("title"):
                continue
            x = int(shape.left / deck.slide_width * 1600)
            y = int(shape.top / deck.slide_height * 900)
            w = int(shape.width / deck.slide_width * 1600)
            h = int(shape.height / deck.slide_height * 900)
            draw.rounded_rectangle((x, y, x + w, y + h), radius=10, outline="#6B7A90", fill="#EEF4F8")
            draw.multiline_text((x + 8, y + 8), shape.text, fill="#233044", font=body_font, spacing=6)
        canvas.save(output / f"page-{page_number:02d}.png")


def test_huangshi_controlled_acceptance_builds_six_editable_pages_without_ui() -> None:
    if not WORD.is_file() or not LOGO.is_file():
        pytest.skip("user-supplied Huangshi acceptance files are not present")
    assert _sha256(WORD) == WORD_SHA256
    assert _sha256(LOGO) == LOGO_SHA256

    source = extract(WORD, DEFAULT_MARKER)
    assert source["page_count"] == 42
    assert tuple(page["page_number"] for page in source["pages"] if page["page_number"] in SELECTED) == SELECTED

    OUTPUT.mkdir(parents=True, exist_ok=True)
    svg = OUTPUT / "shangrong-logo-test-wrapper.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="46">'
        f'<image width="220" height="46" href="data:image/png;base64,{base64.b64encode(LOGO.read_bytes()).decode("ascii")}"/>'
        "</svg>",
        encoding="utf-8",
    )
    assert _sha256(LOGO) == LOGO_SHA256

    confirmation = {
        "test_only": True,
        "ui_bypassed": True,
        "sealed": True,
        "selected_pages": list(SELECTED),
        "word_sha256": WORD_SHA256,
        "logo_sha256": LOGO_SHA256,
    }
    (OUTPUT / "sealed-confirmation.json").write_text(json.dumps(confirmation, ensure_ascii=False, indent=2), encoding="utf-8")
    deck_path = OUTPUT / "huangshi-selected-pages-v1.2.3.pptx"
    _build_deck(source, deck_path)
    _render_previews(deck_path, OUTPUT / "previews")

    deck = Presentation(deck_path)
    assert len(deck.slides) == 6
    assert all(not any(shape.has_chart for shape in slide.shapes) for slide in deck.slides)

    page5 = {shape.name: shape for shape in deck.slides[0].shapes}
    assert len([name for name in page5 if name.startswith("page-5-independent-kpi-")]) == 4
    assert len({page5[name].width for name in page5 if name.startswith("page-5-independent-kpi-")}) == 1

    page10 = {shape.name: shape for shape in deck.slides[1].shapes}
    assert len([name for name in page10 if name.startswith("page-10-independent-kpi-")]) == 6
    assert not any("bar" in name.lower() for name in page10)
    assert "420亿元" in page10["page-10-independent-kpi-1"].text
    assert "100亿元" in page10["page-10-independent-kpi-2"].text
    assert "3—5家" in page10["page-10-independent-kpi-6"].text

    page14 = {shape.name: shape for shape in deck.slides[2].shapes}
    stages = [shape for name, shape in page14.items() if name.startswith("page-14-stage-")]
    assert len(stages) == 7 and len({shape.width for shape in stages}) == 1
    assert not any("axis" in name.lower() or "gantt" in name.lower() for name in page14)

    page20 = {shape.name: shape for shape in deck.slides[3].shapes}
    hierarchy = [shape for name, shape in page20.items() if name.startswith("page-20-node-")]
    assert len(hierarchy) == 8 and len({shape.width for shape in hierarchy}) == 1
    assert not any("mekko" in name.lower() or "treemap" in name.lower() for name in page20)

    page21 = {shape.name: shape for shape in deck.slides[4].shapes}
    assert page21["page-21-disclosed-fact-1"].width == page21["page-21-disclosed-fact-2"].width
    assert "数据缺口" in page21["page-21-data-gap-warning"].text
    assert not any("comparison" in name.lower() or "arithmetic" in name.lower() for name in page21)

    page40 = {shape.name: shape for shape in deck.slides[5].shapes}
    segments = [page40[f"page-40-segment-{index}"] for index in range(1, 4)]
    assert len({shape.width for shape in segments}) == 1
    assert "12个月" in page40["page-40-twelve-month-milestone"].text
    assert page40["page-40-twelve-month-milestone"].top > max(shape.top + shape.height for shape in segments)

    limitations = {
        "unsupported_from_real_manuscript": ["line", "scatter", "bubble", "waterfall", "true_mekko"],
        "reason": "selected manuscript pages do not supply the complete comparable dimensions required for these quantitative encodings",
        "deficiencies_fixed": [
            "kept page 10 same-unit figures separate because their bases differ",
            "made page 21 data-gap warning visible and prevented implied arithmetic",
            "separated page 40 twelve-month milestone from the three source-backed 30-day segments",
        ],
        "remaining_limitations": ["acceptance previews are deterministic test renders, not Image2 stylistic judgments"],
    }
    (OUTPUT / "acceptance-findings.json").write_text(json.dumps(limitations, ensure_ascii=False, indent=2), encoding="utf-8")
    assert all((OUTPUT / "previews" / f"page-{page:02d}.png").is_file() for page in SELECTED)
