# A.2 最终CI记录

代码交付：[`642984426e8faa251a776cedee47d48c8529d918`](https://github.com/qoxgain/jianwei/commit/642984426e8faa251a776cedee47d48c8529d918)。[运行41784713041](https://github.com/qoxgain/jianwei/actions/runs/41784713041)三个作业全部success。

| 作业 | 实际结果 | 记录 |
|---|---|---|
| foundation | 后端679通过、3项缺torch跳过、0失败；前端99、参考29；双构建、历史Demo及A.2证据包校验通过 | [JUnit](ci/foundation-junit.xml) · [作业](https://github.com/qoxgain/jianwei/actions/runs/41784713041/job/126424106014) |
| pretrained-encoder | 安装PyTorch后9通过、0跳过；官方冻结编码器导出验证通过 | [JUnit](ci/encoder-junit.xml) · [作业](https://github.com/qoxgain/jianwei/actions/runs/41784713041/job/126424106335) |
| browser | 全部旧浏览器步骤、216静态/8preview、五状态、A.2普通文本完整性/HMR/map和新离线Demo通过 | [作业](https://github.com/qoxgain/jianwei/actions/runs/41784713041/job/126424106253) · [优化缓存](ci/optimizer.json) · [离线](ci/offline.json) · [静态](ci/static.json) · [五状态](ci/live.json) |

[精确汇总](ci/summary.json)。基础跳过项是test_numpy_preprocessing_matches_pinned_official_transform的shape0/shape1/shape2参数，原因是没有安装torch；独立编码器作业安装后覆盖。不能将基础跳过写成通过。本地安装PyTorch，全量682通过、0跳过。

A.2优化缓存脚本只用无敏感内容的普通文本标记。原媒体/Base64精确反例按用户最新要求没有重复运行，**CI绿色不代表该专项或独立工程验收通过**。独立验收仍pending，B/C/D和正式P0.4未启动。

本次为完成CI后的仅文档证据补录，应用、测试、构建、Demo与受测6429844保持相同内容。补录提交使用[skip ci]避免重复运行相同代码，CI结论明确对应上述代码提交。
