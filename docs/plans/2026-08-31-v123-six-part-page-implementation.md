# v1.2.3 Six-Part Page Design Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 保留现有六段和插件路线，提高整页关系设计与指令传递质量，并查明比例偏差处理；不通过裁正文、补白、拉伸或增加预算伪装修复。

**Architecture:** 六段仍是唯一生图执行设计；在现有导演、编译器和审核接口内修订职责，不建规则引擎。图表选型说明交给导演，生图消费本页具体设计和必要边界。尺寸支线独立诊断，方案未验证前不改适配策略。

**Tech Stack:** 现有Python、pytest、Pillow、Codex订阅调用、Image2、PowerPoint只读渲染；不增加依赖。

---

## 状态和执行边界

本文件是最小实施计划，不是完成报告。Task 1—4可按既定方向实施；Task 5含明确的行为决策闸口；Task 6须待相关代码验证及尺寸支线处理方向明确后开展。当前仅编写计划，未执行代码修改或重新生图。

项目根目录为 D:/AI项目管理/01-当前项目/黄石/awesome-editable-ppt-workflow/.worktrees/semantic-visual-expansion-v1.2.2-clean，以下命令均在此执行。当前分支feat/v1.2.3-huangshi-acceptance-release，计划前HEAD为199083cbbcafcf269c1fa967cffb6964d0760d0b，存在此前未提交改动。先记录工作树差异；禁止reset或把所有现有变更一并提交。各任务仅提交归属于本次任务的差异，不能隔离时保留未提交并报告。

阅读同目录2026-08-31-v123-six-part-page-design.md及现有coherent-page-contract设计。该计划不覆盖1.2.2、原有增页、封面、数值权威、收费/授权、发布或安装。

## Task 1：锁定六段与关系传递的回归

**Files:**
- Modify/Test: plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py
- Modify/Test: plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_director.py

1. 保留现有六段名称/顺序、固定层、schema及事实边界的断言。
2. 增加针对编译输出的最小测试，例子直接复用现有helper：

```python
def test_specific_page_relations_survive_without_global_chart_catalog():
    module = _load_compiler_module()
    value = _director_value()
    relationship = (
        "三类需求并列，共同通过拟转化为连接资产项目。"
        "可测算、可投资、可运营是共同属性。"
        "投前退出设计是限定安排，不是投资后的最后一步。"
    )
    value["prompt_sections"]["consulting_information_architecture"] = relationship
    prompt = module.compile_consulting_six_part_prompt(value, _material_view())
    assert "".join(relationship.split()) in "".join(prompt.split())
    assert "increase_decrease_drivers:" not in prompt
    assert "market_size_share:" not in prompt
    assert len([line for line in prompt.splitlines() if line.startswith("## ")]) == 6
```

3. Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py -q`
   Expected：新增测试因旧版无条件注入图表选型目录而失败；六段现有断言继续通过。
4. 在现有捕获director请求的测试中检查新的正向设计要求同时覆盖信息关系、区域容纳和六段完整交付；不要伪造一次模型成功来证明行为稳定。

## Task 2：在原导演中设计整页，不锁定Word外观

**Files:**
- Modify: plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/director.py
- Modify: plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/references/visual_director.md
- Test: Task 1中的test_director.py

1. 替换“保留source structure”的易误解表述：保留成员、顺序、对应、条件和归属；允许改变原文表格/段落外观，不预先禁用忠实的替代表达。
2. 六段作为唯一执行设计；分析字段中的关键关系须在六段中落地，不依赖Image2看到未发送的creative_direction。不要把整套分析字段再次追加到图片提示词。
3. 用已有task_and_canvas表达每页区域位置和相对空间，结合完整文案预计的行数与图片需求；不采用统一区域比例，不改变编译器拥有的固定几何边界。
4. 用已有consulting_information_architecture明确并列/共同目标/属性/条件的关系，连接线不得靠模型猜测。允许连续解释，反碎片化不等于反图形化。
5. 相同要求覆盖现有模型纠错请求；再生成时可重新安排空间，但不得丢失此前正确的含义。暂不改变既有机械纠错路由及预算。
6. Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_director.py -q`
   Expected：初始导演、模型纠错、完整Word/任务书输入、参考素材和固定边界测试通过；参考文件仍满足现有长度上限。
7. 查看本次diff后单独提交归属本任务的改动；不顺带提交此前的语义合同修改。

## Task 3：精简编译内容，六段结构不变

**Files:**
- Modify: plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/consulting_prompt.py
- Modify: 同目录director.py（仅引用已有选型工具箱）
- Test: Task 1两个测试文件

1. 八类图表选择说明仍保留在已有模块中，由导演选择阶段引用；移除对每张图片的无条件注入。不新增外部工具箱文件或按关键词猜图表类型的路由。
2. 生图保留本页已选的具体关系和表达、简短语义/定量边界、固定层、用户颜色与字体约束。不能为缩短文本删事实、删除数值门槛或牺牲必要限定。
3. 改动仅涉及已有注入点；不新增第七段、不改变prompt_sections/schema。不要建立creative_direction和六段同时发号施令的双重设计源。
4. Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_director.py -q`
   Expected：Task 1新增测试变绿；原有六段、颜色、事实和授权断言通过。若旧测试明确要求每页注入全目录，只替换该已批准的行为断言，不能删除无关回归。
5. 比较三页实际编译结果，报告删去的是通用选择说明，不是来源文案；字符数只是辅助证据，不作为质量评分。

## Task 4：审核对照同页设计，不提高泛化门槛

**Files:**
- Inspect/Modify only if needed: plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/review.py
- Test: plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_review.py
- Regression: 同目录test_loop.py

1. 确认现有审核已收到完整Word和最终六段提示词；复用该输入，不添加新审核上下文或阶段。
2. 对第29页型场景明确审核“并列需求共同指向资产项目”“投前限定未变成末尾阶段”；对第14、21页不得仅因使用表格、连续文字或没有命名图表而拒绝。
3. 如已有要求已足够，保留review.py不改，只补必要回归；不能为了凑修改项增加新规则。
4. Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_review.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_loop.py -q`
   Expected：事实遗漏、关系改变仍被拦截；合理图文混排、同义重组不被误杀；最多两次纠错不变。

## Task 5：尺寸支线先做证据闭环，不擅自改变策略

**Files:**
- Inspect: plugins/awesome-editable-ppt-workflow/skills/generate-slide-body-image/scripts/codex_gpt_image.py（build_image_body、write_images及响应journal）
- Inspect: 同目录provider_worker.py（请求校验和发出原始body字节）
- Test: plugins/awesome-editable-ppt-workflow/skills/generate-slide-body-image/tests/test_off_ratio_output_policy.py
- Test: 同目录test_generation_trace.py
- Integration regression: plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_image.py

1. 只读利用本次已保存的签名journal核对原始图，不新调模型，不打印凭据或整个响应base64。本轮已核实1601×982和1647×955为原始响应尺寸，不是重建阶段引起。
2. 用现有_encoded_png测试helper复现这两组尺寸并确认当前裁切范围。该测试揭示现状，不代表批准继续裁正文。
3. 检查本地请求参数构建、worker校验和发送字节是否一致。已读代码显示size未丢失；没有证据时，不虚构通过修改某个参数即可保证服务返回17:8。
4. 若找到可验证的本地缺陷，写最小失败测试后修复；若仍为服务返回偏离而无已验证处理方式，停止本支线行为修改，向用户报告需要决定的具体策略。不得把硬失败、超额重试、补白、拉伸、新服务或新模型调用当作默认方案。
5. 不改写旧项目候选、journal和审核收据；不把重新放大已有裁切图当恢复原文。原始资料复用以诊断为限，不绕过正式生成与接受流程交付PPT。
6. Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/generate-slide-body-image/tests/test_off_ratio_output_policy.py plugins/awesome-editable-ppt-workflow/skills/generate-slide-body-image/tests/test_generation_trace.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_image.py -q`
   Expected：先得到可复现结果，再依据经过确认的策略定义新预期；没有确认策略前不通过修改断言宣称修复。

## Task 6：实际PPT验证与交付

1. 运行以上定向测试及原有composition、特殊页、定量重建回归。命令：
   `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_composition.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_current_editable_page_package.py -q`
   不声称两项命令代表全套测试，相关封面与数值测试按本次实际影响补跑并记录。
2. `git diff --check`检查补丁。记录修改文件与未改边界。Python运行环境使用已验证的应用自带环境，避免重新触发用户site-packages可见性差异，不改全局安装。
3. 独立快照、独立项目，复用确认过的Word、Logo、样式和任务书，沿用原项目初始化/确认提交/材料发布入口。不可手改工作流状态；不得覆盖compare-v123-coherent-rules-20260831。
4. 正式命令：`python scripts/word_to_editable_ppt.py v6 run-pages --project <新的绝对项目路径> --pages 14 21 29`，在新快照的run-word-to-ppt-workflow目录执行。使用应用自带Python的绝对路径替换python。自动调用和纠错额度维持原值。
5. 审核通过后由现有worker重建；实际PowerPoint打开与渲染。逐页对照完整Word、接受图、可编辑成品，分别报告信息遗漏、关系、构图、裁切与重建偏差。使用此前只读预览工具，不手写替代PPT。
6. 交付前明确：哪些页成品、哪些失败、哪些仍需用户选择。单次三页结果不等于稳定性证明；尺寸支线未闭环时不得称整体目标实现。

## 不选的方案

不更换六段体系；不把整个分析JSON再拼到Image2请求；不新增布局引擎或审核代理；不靠提高重试数量弥补设计；不采用已拒绝的补背景留白。保留最小可回退修改，不改1.2.2。
