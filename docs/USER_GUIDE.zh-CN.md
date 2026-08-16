# 使用说明（awesome-word-ppt-workflow-v1）

## 一次最终确认

每个新项目需要一个分页 Word 和一个 SVG Logo；一页 Word 固定对应一页 16:9 PPT。UI 中只进行一次最终提交。该提交是 sole material/reference authority（材料与参考图唯一权威）：本页有效正文、附件提取、图表事实、具体生图要求和参考图选择都会冻结，后台不得再次解释批注、增删事实或改写用户确认内容。

每张候选参考图都必须 explicit keep/remove（明确保留或移除）。无法访问的附件和已失败的单次搜索会显示降级状态，但不阻断。

## 自适应 Image2

没有确认参考图时使用 `generate`；有 `1–16 confirmed refs` 时使用 `edit`，并按确认顺序携带原图和用途。真实 Logo、截图和照片只能做到 high-fidelity best effort（高保真尽力融合），never pixel-perfect（绝不承诺像素级一致）。图表可转换为文字事实后参与生成。

请求仍为 1904x896；服务端原始尺寸和真实 quality 会原样记录。插件根据真实宽高执行 dynamic centered 17:8 crop（动态居中 17:8 裁切），再等比缩放到 1904x896，不拉伸、不填透明边框。

## QA、重建与固定层

裁切缩放后的候选只接受 single independent visual review（一次独立视觉评审）。只有评审明确接受的候选才能重建；明确拒绝后才允许 at most two corrections（最多两次修正），评审不可用或修正耗尽会停止该页，不强制接受任何候选。

接受的正文图由同一 Codex page worker（Codex 页面 worker）进行对象级可编辑重建；文字、简单形状和连接关系成为原生对象，照片、人物、Logo 和复杂插画保持为独立图片对象。固定页面标题、original SVG logo、页脚和页码始终作为 PPT 原生层添加。V6 没有 V4/V5 runtime fallback、整页截图兜底、exact overlay 或 post-reconstruction visual repair。

重建可能需要独立的 `editppt` authentication。Image2 可正常不代表重建令牌也有效；认证错误请按故障排除处理。
