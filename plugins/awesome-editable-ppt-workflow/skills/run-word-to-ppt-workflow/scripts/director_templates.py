"""Five whole-deck director templates and deterministic recommendation logic."""

from __future__ import annotations

import copy
from typing import Any

from director_taskbook import validate_taskbook


DIRECTOR_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "company-business-introduction",
        "name": "公司及业务推介材料",
        "description": "帮助受众快速理解公司、业务价值与合作理由。",
        "director_summary": "以清晰价值主张串联公司能力、业务结构、成果证据与合作空间。",
        "defaults": {
            "primary_color": "#17365D", "secondary_color": "#2F6FB0",
            "highlight_color": "#2F6FB0",
            "background_color": "#FFFFFF", "cjk_font": "Microsoft YaHei",
            "latin_font": "Arial", "title_size_pt": 30, "body_size_pt": 14,
            "caption_size_pt": 9,
        },
        "taskbook": {
            "use_scenario": "公司及业务推介",
            "presenter": "公司管理团队或业务负责人",
            "primary_audience": "潜在客户、合作伙伴或其他外部利益相关方",
            "audience_prior_knowledge": "对公司和业务仅有基础认知",
            "desired_outcome": "形成对公司价值、业务能力和合作空间的清晰理解",
            "emphasis": "公司定位、核心业务、差异化能力、已有成果与合作价值",
            "deemphasis": "与受众决策无直接关系的内部过程细节",
        },
    },
    {
        "id": "investment-committee",
        "name": "投决会材料",
        "description": "围绕投资判断、回报、风险与决策条件组织证据。",
        "director_summary": "以明确投资判断为主线，呈现关键事实、估值回报、风险边界和待决事项。",
        "defaults": {
            "primary_color": "#13263A", "secondary_color": "#B58A2A",
            "highlight_color": "#B58A2A",
            "background_color": "#FFFFFF", "cjk_font": "Microsoft YaHei",
            "latin_font": "Arial", "title_size_pt": 28, "body_size_pt": 13,
            "caption_size_pt": 9,
        },
        "taskbook": {
            "use_scenario": "投资决策委员会审议",
            "presenter": "项目投资团队",
            "primary_audience": "投资决策委员会",
            "audience_prior_knowledge": "已掌握项目基础情况并关注关键判断依据",
            "desired_outcome": "就投资方案、条件或后续安排形成决定",
            "emphasis": "投资逻辑、核心证据、估值与回报、关键风险和决策条件",
            "deemphasis": "不影响投资判断的重复背景介绍",
        },
    },
    {
        "id": "project-initiation",
        "name": "立项会材料",
        "description": "说明立项依据、初步判断、待验证事项与下一步工作。",
        "director_summary": "围绕是否值得投入下一阶段资源，区分已有事实、初步判断和后续验证任务。",
        "defaults": {
            "primary_color": "#244A73", "secondary_color": "#D07A2D",
            "highlight_color": "#D07A2D",
            "background_color": "#FFFFFF", "cjk_font": "Microsoft YaHei",
            "latin_font": "Arial", "title_size_pt": 28, "body_size_pt": 13,
            "caption_size_pt": 9,
        },
        "taskbook": {
            "use_scenario": "投资项目立项审议",
            "presenter": "项目发起团队",
            "primary_audience": "立项评审人员和相关负责人",
            "audience_prior_knowledge": "了解机会来源，但尚未形成完整项目判断",
            "desired_outcome": "决定是否立项以及下一阶段工作范围",
            "emphasis": "立项理由、初步价值判断、关键假设、待尽调事项和资源安排",
            "deemphasis": "尚无材料支持的确定性结论",
        },
    },
    {
        "id": "corporate-planning",
        "name": "公司规划报告",
        "description": "把战略目标、重点任务、实施路径与衡量方式连成体系。",
        "director_summary": "由现状判断推导目标和选择，再落到行动、责任、节奏与结果衡量。",
        "defaults": {
            "primary_color": "#17212B", "secondary_color": "#176B67",
            "highlight_color": "#D3A62C",
            "background_color": "#F7F6F2", "cjk_font": "Microsoft YaHei",
            "latin_font": "Arial", "title_size_pt": 30, "body_size_pt": 14,
            "caption_size_pt": 9,
        },
        "taskbook": {
            "use_scenario": "公司战略与经营规划汇报",
            "presenter": "公司管理层或规划负责人",
            "primary_audience": "董事会、管理层和关键执行负责人",
            "audience_prior_knowledge": "熟悉公司现状，需要统一未来方向和行动重点",
            "desired_outcome": "对规划目标、关键选择和实施重点形成共识",
            "emphasis": "现状判断、战略目标、关键选择、重点任务和实施路径",
            "deemphasis": "不能支撑规划选择的零散事项罗列",
        },
    },
    {
        "id": "investment-project-bp",
        "name": "投资项目 BP",
        "description": "面向投资人建立项目价值、增长逻辑与融资用途的完整认知。",
        "director_summary": "以项目机会和增长逻辑为主线，用产品、市场、团队、财务与融资证据支撑价值判断。",
        "defaults": {
            "primary_color": "#101820", "secondary_color": "#E85D2A",
            "highlight_color": "#E85D2A",
            "background_color": "#FFFFFF", "cjk_font": "Microsoft YaHei",
            "latin_font": "Arial", "title_size_pt": 30, "body_size_pt": 14,
            "caption_size_pt": 9,
        },
        "taskbook": {
            "use_scenario": "投资项目融资或商业计划推介",
            "presenter": "项目创始团队或融资负责人",
            "primary_audience": "潜在投资人和投资机构",
            "audience_prior_knowledge": "对项目有初步兴趣但需要完整理解投资价值",
            "desired_outcome": "形成进一步沟通、尽调或投资意向",
            "emphasis": "市场机会、解决方案、商业模式、增长证据、团队能力和资金用途",
            "deemphasis": "缺少材料依据的夸张承诺",
        },
    },
)

SIGNAL_TERMS = {
    "company-business-introduction": ("公司介绍", "业务介绍", "业务推介", "合作", "客户", "能力", "优势"),
    "investment-committee": ("投委会", "投资决策", "估值", "回报", "退出", "收益率", "决策"),
    "project-initiation": ("立项", "尽调", "可行性", "初步判断", "工作计划", "项目机会"),
    "corporate-planning": ("规划", "战略", "目标", "重点任务", "实施路径", "经营计划"),
    "investment-project-bp": ("bp", "融资", "商业计划", "市场空间", "商业模式", "资金用途", "投资人"),
}


def recommend_director(signal_text: str) -> dict[str, Any]:
    text = signal_text.lower()
    scores = {
        template["id"]: sum(text.count(term.lower()) for term in SIGNAL_TERMS[template["id"]])
        for template in DIRECTOR_TEMPLATES
    }
    selected = max(
        range(len(DIRECTOR_TEMPLATES)),
        key=lambda index: (scores[DIRECTOR_TEMPLATES[index]["id"]], -index),
    )
    template = DIRECTOR_TEMPLATES[selected]
    score = scores[template["id"]]
    confidence = "high" if score >= 3 else "medium" if score >= 1 else "low"
    matched = [term for term in SIGNAL_TERMS[template["id"]] if term.lower() in text]
    reason = (
        f"Word 材料出现了与“{template['name']}”相关的内容：{'、'.join(matched[:4])}。"
        if matched else f"Word 材料未出现明确场景信号，默认推荐“{template['name']}”。"
    )
    return {
        "recommended_template_id": template["id"],
        "recommendation_reason": reason,
        "recommendation_confidence": confidence,
        "director_taskbook": validate_taskbook(copy.deepcopy(template["taskbook"])),
    }


def public_templates() -> list[dict[str, Any]]:
    return [
        {key: copy.deepcopy(template[key]) for key in ("id", "name", "description", "director_summary", "defaults")}
        for template in DIRECTOR_TEMPLATES
    ]


def taskbook_for_template(template_id: str) -> dict[str, str]:
    for template in DIRECTOR_TEMPLATES:
        if template["id"] == template_id:
            return validate_taskbook(copy.deepcopy(template["taskbook"]))
    raise ValueError("unknown director template")
