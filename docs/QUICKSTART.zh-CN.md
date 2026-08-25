# Awesome Editable PPT Workflow 1.2.1 快速开始

下载不可变的 `v1.2.1` Windows Release ZIP：

`https://github.com/czd176224-rgb/awesome-editable-ppt-workflow/releases/download/v1.2.1/awesome-editable-ppt-workflow-1.2.1-windows.zip`

同时下载 `SHA256SUMS.txt`，使用 PowerShell `Get-FileHash` 核验后解压并运行 `install.ps1`，然后重启 Codex。

使用时提交一份已分页的 `.docx` 和一份 `.svg` Logo。UI 只确认一次：

1. 选择系统推荐的整篇页面导演；
2. 确认主题色、辅助色、背景色、字体和字号；
3. 确认本次场景任务书。

Word 始终是页数、顺序、标题、正文和事实的权威。插件不会增加、删减、改写或移动 Word 材料。

如需明确特殊页，可在对应 Word 页加入一个独立段落或批注：

- `PPT页型：封面`
- `PPT页型：目录`
- `PPT页型：章节`
- `PPT页型：正文`
- `PPT页型：尾页`
- `PPT页型：附录`

控制行不会显示在 PPT 中。没有控制行时，插件只做保守识别，不会凭空生成特殊页。
