# 常见故障（V6 adaptive）

## 最终 UI 无法提交

每张 staged reference 都必须 explicit keep/remove。检查所有页面的材料 JSON、参考图决定和修订号；最终提交是 sole material/reference authority，提交后后台不会静默修改。

## Image2 输出比例错误

请求尺寸为 1904x896，但服务端可能返回其他画布。检查 trace 中的请求尺寸、服务端原始尺寸、真实 quality、dynamic centered 17:8 crop 裁切框和最终 1904x896 尺寸；插件只做居中裁切与等比缩放，不拉伸。

## 真实图片不像原图

有 `1–16 confirmed refs` 时会走 `edit`，但融合属于 high-fidelity best effort，never pixel-perfect。减少互相冲突的参考图、明确每张图用途，并确保使用的是最终 UI 中确认的原图。

## 独立视觉评审不可用

评审不可用时该页停止，不会接受未评审候选，也不会消耗额外修正机会。恢复服务后从现有安全状态继续；只有明确拒绝才允许 at most two corrections。

## 重建返回 `401 token_expired`

对象级重建使用独立 editppt authentication，可能与 Image2 登录状态不同。`401 token_expired` 表示该令牌已过期；重新完成 editppt/Codex 登录后重试重建。不要声称该状态下已完成在线重建。

## 固定标题、Logo、页脚或页码异常

这些内容都是 PPT 固定层：标题、original SVG logo、页脚、页码不应出现在 Image2 正文中。V6 不提供 V4/V5 runtime fallback、exact overlay 或 post-reconstruction visual repair。

## 安装后仍显示旧版本

完全退出并重启 Codex Desktop，再新建任务。运行 `verify.ps1` 查看插件版本和运行时诊断；不要手工修改个人 marketplace 配置。
