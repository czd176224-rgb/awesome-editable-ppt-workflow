# Awesome Editable PPT Workflow 1.2.3 快速开始

下载不可变的 `v1.2.3` Windows Release ZIP：

`https://github.com/czd176224-rgb/awesome-editable-ppt-workflow/releases/download/v1.2.3/awesome-editable-ppt-workflow-1.2.3-windows.zip`

同时下载 `SHA256SUMS.txt`，使用 PowerShell `Get-FileHash` 核验后解压并运行 `install.ps1`，然后重启 Codex。

使用时提交一份已分页的 `.docx` 和一份 `.svg` Logo。UI 只确认一次：

1. 选择系统推荐的整篇页面导演；
2. 确认主题色、辅助色、背景色、字体和字号；
3. 确认本次场景任务书。

确认的背景色会作为所有最终 PPT 页面的整页背景色。系统会在后台把任务书中确认的重点内容与 Word 各页匹配，只允许匹配到的重点页使用辅助色同色系字体；非重点页仍可使用同色系形状、线条和文本框底色，并通过加粗、字号、位置和层级强调文字。

Word 始终是页数、顺序、标题、正文和事实的权威。插件不会增加、删减、改写或移动 Word 材料。

当 Word 提供完整且同口径的数值维度时，插件可重建原生可编辑图表或可编辑特殊图形；数据不完整时只使用非比例的路线图、对比表、等宽层级等替代结构，不补数字、不制造比例。

如需明确特殊页，可在对应 Word 页加入一个独立段落或批注：

- `PPT页型：封面`
- `PPT页型：目录`
- `PPT页型：章节`
- `PPT页型：正文`
- `PPT页型：尾页`
- `PPT页型：附录`

控制行不会显示在 PPT 中。没有控制行时，插件只做保守识别，不会凭空生成特殊页。
