# P0.2 实际浏览器验收

执行日期：2026-10-23。使用实际系统 Chromium（版本与两份构建 SHA256 见 [results.json](results.json)），通过 Playwright 操作页面；截图来自本次构建，未使用示意图。桌面为 1440×1000，手机为 390×844。

**结果：API 与独立演示两模式通过，页面异常和控制台错误均为 0。**

| 检查 | API | 独立 HTML 静态服务 |
|---|---|---|
| 四场景示例、120 行特征、2 个完整周期 | 实际服务端导入通过 | 展示已生成的真实示例清单 |
| 重复操作复用同一记录 | 通过 | 通过 |
| 刷新恢复与 JSON 清单下载 | 通过 | 通过 |
| 桌面与手机宽度、内容可见性 | 通过 | 通过 |
| MP4 文件上传、32 帧实际解码 | 通过；缺权重明确 blocked | 上传禁用，提示启动完整应用 |
| 损坏 MP4 | 明确 failed，说明解码失败 | 不运行服务端处理 |
| 外部请求检查 | 实际连接隔离 API | 仅同源同路径 HTML 文档导航，无 API/资源请求 |
| 旧知识工作台四场景回归 | 通过 | 通过 |

视频由 FFmpeg `testsrc2` 生成，仅为文件处理测试图；虽然通过正常文件上传入口导入，它不是现场录像，不证明视觉识别或学习效果。NPZ 示例为确定性合成特征；测试没有产生动作标签、识别准确率或训练收益。API 数据库与上传目录位于独立的 `artifacts/p02-browser-work`，未使用或重置用户的 `data_demo`。

## 查看截图与证据

- [API 无标签观察数据接入](api-anonymous_observation.png)
- [独立演示手机清单](offline-anonymous_observation-mobile-results.png)
- [MP4 解码完成、等待真实权重](api-video-awaiting-weights.png)
- [损坏视频的明确失败提示](api-invalid-video.png)
- [电子装配重复流程节点回归](api-electronics_assembly-knowledge.png)
- [空知识手机回归](offline-anonymous_observation-mobile-knowledge.png)
- [数据接入机器可读结果](results.json) · [旧知识工作台结果](knowledge-results.json)
- [数据接入执行日志](logs/ingestion-browser.log) · [知识工作台执行日志](logs/knowledge-browser.log)

同目录保存四场景两模式的桌面、手机截图和浏览器实际导出的 manifest。视觉检查抽查了无标签场景桌面接入页面及离线手机清单；哈希换行、周期表格、状态说明和导出按钮均可见。

## 复现

先构建两模式前端，并以独立配置启动 API 与纯静态服务。当前命令仅用于开发机内部验收，不是公网访问链接：

```bash
python tests/browser_ingestion.py --url http://127.0.0.1:18021/ --demo http://127.0.0.1:18022/ --chromium /usr/bin/chromium
python tests/browser_scenes.py --url http://127.0.0.1:18021/ --demo http://127.0.0.1:18022/ --chromium /usr/bin/chromium
```

浏览器脚本不由 pytest 自动执行。企业浏览器若禁止 `file://`，使用本机纯静态 HTTP 服务即可；本次没有绕过浏览器策略。
