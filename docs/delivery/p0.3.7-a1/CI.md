# P0.3.7-A.1 最终CI记录

代码交付提交：[`5f150e7daf982c3cb0c9066691f25e7b34961bc8`](https://github.com/qoxgain/jianwei/commit/5f150e7daf982c3cb0c9066691f25e7b34961bc8)。[最终运行41753446076](https://github.com/qoxgain/jianwei/actions/runs/41753446076)三个作业均为success；包含R02-2回调补修e688ba1。

| 作业 | 实际结果 | 证据 |
|---|---|---|
| foundation | 后端635通过、3跳过、0失败；前端99、参考29；双构建及历史/当前Demo摘要核验通过 | [JUnit](ci/foundation-junit.xml) · [作业](https://github.com/qoxgain/jianwei/actions/runs/41753446076/job/126323819245) |
| pretrained-encoder | 安装PyTorch后9通过、0跳过；官方冻结编码器导出验证通过 | [JUnit](ci/encoder-junit.xml) · [作业](https://github.com/qoxgain/jianwei/actions/runs/41753446076/job/126323819606) |
| browser | 四场景、采样、协议、P0.3.6/原A及原视频回查；A.1静态216变体、双构建/preview/缓存；两I/O令牌五状态；离线筛选/导出全部通过 | [作业](https://github.com/qoxgain/jianwei/actions/runs/41753446076/job/126323819845) · [静态](ci/static.json) · [实时交互](ci/live-browser.json) · [离线](ci/offline-browser.json) |

[精确汇总JSON](ci/summary.json)。基础作业跳过的3项是test_numpy_preprocessing_matches_pinned_official_transform的shape0/shape1/shape2参数：未安装torch。独立编码器作业安装torch并覆盖这些用例，不能把基础作业跳过本身写成通过。本地安装PyTorch，638通过、0跳过；本地浏览器Chromium154，远端Chrome156。

首轮ca34403的[运行41620188403](https://github.com/qoxgain/jianwei/actions/runs/41620188403)也通过（630通过、3跳过；独立编码器9通过），但随后发现公开row_factory回调会泄露原生目标能力，已单独复现、修补并以本次最终运行重新验证。不能用首轮绿色结果替代最终整改或独立验收。

本记录为测试完成后的仅文档证据补录；应用、测试、构建与已验收Demo均保持5f150e7的内容。补录提交使用[skip ci]避免对相同代码重复运行，CI结论明确指向上述代码提交。独立验收仍pending；B/C/D及正式P0.4未启动。
