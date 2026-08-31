# Coherent Page Contract Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 v1.2.3 中同步无损语义重组、页面功能与双向审核口径，不改变一页对一页或既有运行拓扑。

**Architecture:** 复用 `consulting_prompt.py` 的共享约束模式；初始导演、修正、生图与 Reviewer 引用同一语义合同。重建继续只转写已接受图片及锁定数值，不成为第二个导演。

**Tech Stack:** Python、pytest、现有 Markdown 提示词和 JSON schema（结构不改）。

---

以下路径相对当前 worktree。前缀 W = `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow`，R = `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide`。

## Task 1: 锁定合同冲突

文件：W/tests/complex_page_experiment/test_consulting_prompt.py、test_director.py、test_review.py。

1. 运行现有三组测试，记录基线。
2. 在真实编译/请求捕获边界更新断言：忠实改写与全信息保留同在；结构页无需人为结论；修正和初始导演合同一致；Reviewer 不按逐字匹配拒绝，并保留完整原文作为权威。
3. 运行 `python -m pytest W/tests/complex_page_experiment/test_consulting_prompt.py W/tests/complex_page_experiment/test_director.py W/tests/complex_page_experiment/test_review.py -q`（将 W 展开为上面的真实路径）。旧实现应因缺少新合同而 FAIL，不因导入或环境问题失败。
4. 独立检查五类内容场景。提示词输出断言只证明发送的合同，不冒充模型语义表现测试。

## Task 2: 同步替换规则

文件：W/scripts/complex_page_experiment/consulting_prompt.py、director.py、review.py、references/visual_director.md；R/prompts/page-worker.md。

1. 在现有编译器定义共享语义合同，替换原 `_VISIBLE_TEXT_CUSTODY`，同时消除元指令边界里的逐字片段限制。
2. 初始导演与修正请求导入同一合同；已有 creative_direction 字段作为页面功能规划，未出现结论时明确不适用，而非发明结论。保留 schema 和调用次数。
3. 替换已加载视觉参考中的强制论证结构与“成员没展示就删总数”等冲突。
4. Reviewer 对完整原文做双向语义核对，沿用已有错误类别和修正次数。保留定量数值检查与正常随机差异容忍。
5. 重建明确忠实转写已接受的改写，不二次改写或恢复 Word 原措辞；固定画布和 numeric_authority 行为不变。
6. 同一命令应全部 PASS；失败时检查遗漏的实际调用路径，不屏蔽安全或数值断言。

## Task 3: 验证与交付

补充实际入口：同步 W/scripts/director_taskbook.py、W/scripts/complex_page_experiment/loop.py 及 test_loop.py；同步 W/SKILL.md 和 R/references/page-decision-tree.md 的职责说明。对 W/scripts/workflow_v6_composition.py 与 W/tests/test_workflow_v6_composition.py 先测试后修复：新项目禁止目录拆页、章节与结束页合成，保留所有源材料及顺序；历史 composition validator/读取兼容，不改既有项目。该独立切片交由子代理实现，主代理集成验证。

1. 扩展运行 W/tests/complex_page_experiment、W/tests/test_accepted_image_worker_reconstruction.py、W/tests/test_huangshi_v123_acceptance.py、W/tests/test_quantitative_chart_v123_e2e.py、W/tests/test_workflow_v6_special_pages.py，以及 R/cli/tests 的定量和 manifest 测试。
2. 执行 `python -m compileall -q` 检查改动 Python 文件；执行 `git diff --check`。
3. 独立审查现有运行规则、实际加载的参考文件及修正路径，查找残留逐字匹配/强制结论规则；历史设计文档保留但标明被本设计取代的口径。
4. 确认改动仅在现有 v1.2.3 worktree；不改变 1.2.2 或测试运行快照、不增加双栏封面。
5. 设计文档单独提交；已有未提交代码与本轮代码保持可审查，不将用户旧修改混入无关提交。不 push、不发布。
6. 记录实际测试数、跳过项及未做真实生成的验证缺口。
