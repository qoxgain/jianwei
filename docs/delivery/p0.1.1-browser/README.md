# P0.1.1 浏览器补充验收

日期：2026-10-23。代码基线：`29690eb0807095dda071bc5f0ab936502f5f8cf4`，加本目录列明的小修复。

**门槛通过。** 使用实际 Chromium 154，API 和自包含 HTML 静态服务两种模式完成四场景操作。桌面为 1440 × 1000，手机为 390 × 844。测试使用隔离 SQLite 数据，不修改已有 `data_demo`。

- 四场景均完成编辑、严格校验、保存候选、刷新后读取候选、确认文件基线不变。
- 电子装配显示独立 `node_3` / `node_5`，均引用 `inspect`。
- 安全和无标签场景没有 SOP 页签或服装扩展；无标签场景保留空动作。
- 手机导航、编辑器可见性与页面宽度通过。截图关闭动画；手机截图为实际视口。
- 无页面异常，无控制台 error；离线模式只请求当前自包含 HTML 文档（允许场景查询参数），不访问 API 或外部资源。
- 旧 smoke 验证动作空值可继续编辑、重复 YAML 拒绝、候选持久化、现场环境显示真实阻塞状态。

修复了缺少 favicon 导致的 404：图标以内嵌 SVG 随 HTML 交付。另修正旧 smoke 对离线文档场景查询参数的误判，扩展场景脚本的手机与控制台检查。没有修改知识工作台业务逻辑。

## 证据

- [场景结果](scenes/results.json) / [场景日志](scenes.log)
- [Smoke 结果](smoke/results.json) / [Smoke 日志](smoke.log)
- [构建](build.log) / [离线构建](build-demo.log) / [构建 SHA 与环境](validation.json)

| 场景 | API 桌面 | API 手机 | 离线桌面 | 离线手机 |
|---|---|---|---|---|
| 服装 SOP | [工作台](scenes/api-garment_sop-knowledge.png) | [概览](scenes/api-garment_sop-mobile.png) | [工作台](scenes/offline-garment_sop-knowledge.png) | [概览](scenes/offline-garment_sop-mobile.png) |
| 电子装配 | [重复节点](scenes/api-electronics_assembly-knowledge.png) | [概览](scenes/api-electronics_assembly-mobile.png) | [重复节点](scenes/offline-electronics_assembly-knowledge.png) | [概览](scenes/offline-electronics_assembly-mobile.png) |
| 车间安全 | [知识](scenes/api-workshop_safety-knowledge.png) | [概览](scenes/api-workshop_safety-mobile.png) | [知识](scenes/offline-workshop_safety-knowledge.png) | [概览](scenes/offline-workshop_safety-mobile.png) |
| 无标签观察 | [空动作](scenes/api-anonymous_observation-knowledge.png) | [概览](scenes/api-anonymous_observation-mobile.png) | [空动作](scenes/offline-anonymous_observation-knowledge.png) | [概览](scenes/offline-anonymous_observation-mobile.png) |

## 复现

先在隔离数据目录中启动 API，并从 `apps/web/dist-demo` 启动纯静态 HTTP 服务。两个端口只是本地验收地址，不是公网预览。

```bash
npm --prefix apps/web run build
npm --prefix apps/web run build:demo
python tests/browser_scenes.py --url http://127.0.0.1:18011/ --demo http://127.0.0.1:18012/ --chromium /usr/bin/chromium
python tests/browser_smoke.py --url http://127.0.0.1:18011/ --demo http://127.0.0.1:18012/ --chromium /usr/bin/chromium
```

本记录仅补齐 P0.1.1 UI 验收，不代表视频识别、训练或真实业务效果通过。页面和数据属于此基线；后续 P0.2 需独立验收。
