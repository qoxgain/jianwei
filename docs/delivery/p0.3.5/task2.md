# 子任务 2：协议与会话统计页面、真实浏览器验收

前置后端提交：a35732b14d598af83108840becfd1b35a1d6c08f。

新增只读协议预览/JSON 导出、固定来源与版本展示、会话/跨度四指标表、两项对照区间与研究门槛显示。切换显示模型/指标不会改变校准时选定的候选。旧报告兼容，缺统计时明确提示；离线协议明确“未核验”。补充实际执行的合成研究 JSON，不替换四场景历史报告，主运行配置没有伪造登记。

修改文件：apps/web/src/ResearchProtocolPanel.tsx、SessionStatistics.tsx、TemporalPanel.tsx、researchTypes.ts、temporalTypes.ts、temporalService.ts、styles.css、researchExample.json、research.test.tsx；tests/browser_research_protocol.py、tests/browser_temporal.py；CI 增加新浏览器检查。

前端 93 passed，两种构建成功。后端无新增修改，沿用子任务 1 的 516 passed/参考 29 passed。真实 Chromium 154 桌面 1440x1000、手机 390x844：四场景协议只读/导出、快速切场景、会话不足、区间显示、四指标切换、固定候选、报告下载均通过。最终构建另完成四场景 API/离线回归，包含失败后原缓存恢复重试、训练/评估、权重与预测日志下载、刷新恢复及手机布局，零 page/console error。

两套不同的合成测试配置分别覆盖“2 个来源低于 3 会话门槛”和“2 个来源满足数学计算下限”分支；只用于检验机制，都保持 mechanism_only，真实会话数 0。没有修改任何真实研究的阈值或冒充现场学习验收。

protocol-browser.json 保存首次新增交互检查时的构建哈希；随后接入已执行记录，browser-regression.json 和 offline-protocol-browser.json 保存最终构建哈希。后者通过纯本机静态服务运行，API/远端请求数为 0；当前云浏览器限制 file://，该限制未算作产品失败或伪报本地文件检查通过。离线 HTML 按说明用静态服务器即可查看。

截图仅为数值表与应用 UI，不含原视频/PDF衍生画面；本目录保存桌面不足样本提示和手机机制报告两张，其余实测截图在本地 artifacts/p035-browser、artifacts/p035-regression。
