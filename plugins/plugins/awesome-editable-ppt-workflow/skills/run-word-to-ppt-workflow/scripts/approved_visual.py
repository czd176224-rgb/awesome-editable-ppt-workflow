"""Render the confirmed UI state into a deterministic audit-only PNG."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/msyhbd.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/System/Library/Fonts/PingFang.ttc"),
)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _hex(value: Any, fallback: str) -> str:
    text = str(value or "").upper()
    return text if re.fullmatch(r"#[0-9A-F]{6}", text) else fallback


def _lines(text: str, width: int, limit: int) -> list[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ["忠实表达本页信息与逻辑"]
    return [compact[index : index + width] for index in range(0, min(len(compact), width * limit), width)]


def render_ui_preview_audit(confirmed: dict[str, Any], title: str, body: str) -> bytes:
    canvas = confirmed.get("canvas")
    size = (1792, 1008) if canvas == "ppt169" else (1536, 1152)
    palette = (confirmed.get("color") or {}).get("palette") or {}
    background = _hex(palette.get("background"), "#FFFFFF")
    secondary = _hex(palette.get("secondary_bg"), "#F2F4F7")
    primary = _hex(palette.get("primary"), "#17365D")
    accent = _hex(palette.get("accent"), "#D97706")
    secondary_accent = _hex(palette.get("secondary_accent"), "#4B74A6")
    body_color = _hex(palette.get("body_text"), "#1F2937")

    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    width, height = size
    margin_x = int(width * 0.065)
    margin_y = int(height * 0.075)
    title_font = _font(max(34, int(height * 0.047)))
    section_font = _font(max(22, int(height * 0.027)))
    body_font = _font(max(18, int(height * 0.021)))
    small_font = _font(max(14, int(height * 0.016)))

    draw.text((margin_x, margin_y), "APPROVED VISUAL SYSTEM", font=small_font, fill=secondary_accent)
    draw.text((margin_x, margin_y + int(height * 0.052)), title[:38] or "演示文稿视觉系统", font=title_font, fill=primary)
    rule_y = margin_y + int(height * 0.135)
    draw.rectangle((margin_x, rule_y, width - margin_x, rule_y + 3), fill=primary)
    draw.rectangle((int(width * 0.76), rule_y, width - margin_x, rule_y + 6), fill=accent)

    content_top = rule_y + int(height * 0.05)
    left_width = int(width * 0.51)
    gap = int(width * 0.035)
    right_x = margin_x + left_width + gap
    draw.text((margin_x, content_top), "核心表达", font=section_font, fill=primary)
    cursor_y = content_top + int(height * 0.055)
    for line in _lines(body, 34, 6):
        draw.text((margin_x, cursor_y), line, font=body_font, fill=body_color)
        cursor_y += int(height * 0.042)
    draw.line((margin_x + left_width, content_top, margin_x + left_width, int(height * 0.82)), fill=secondary_accent, width=1)

    box_bottom = content_top + int(height * 0.22)
    draw.rectangle((right_x, content_top, width - margin_x, box_bottom), fill=secondary)
    draw.rectangle((right_x, content_top, width - margin_x, content_top + 5), fill=accent)
    draw.text((right_x + int(width * 0.02), content_top + int(height * 0.04)), "重点信息", font=section_font, fill=primary)
    draw.text((right_x + int(width * 0.02), content_top + int(height * 0.105)), "清晰层级 · 克制色彩 · 内容驱动", font=body_font, fill=body_color)
    bullets = ("根据内容自主选择版式", "图形必须服务于信息", "保留充分的页面设计空间")
    bullet_y = box_bottom + int(height * 0.065)
    for bullet in bullets:
        draw.ellipse((right_x, bullet_y + 7, right_x + 9, bullet_y + 16), fill=accent)
        draw.text((right_x + 22, bullet_y), bullet, font=body_font, fill=body_color)
        bullet_y += int(height * 0.055)

    draw.rectangle((0, height - 7, width, height), fill=accent)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()
