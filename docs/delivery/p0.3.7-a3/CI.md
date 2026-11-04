# A.3 CI记录

实际受测提交：[fb69646336eb6346ea3cec498a08856a80a34e60](https://github.com/qoxgain/jianwei/commit/fb69646336eb6346ea3cec498a08856a80a34e60)。[GitHub Actions 41804872121](https://github.com/qoxgain/jianwei/actions/runs/41804872121)三个作业全部success；后续仅CI证据文档提交使用skip ci，不冒充重新执行过代码。

| 作业 | 实际结果 |
|---|---|
| foundation | 后端710收集：707通过、3跳过、0失败；新增A.3的28项全部通过 |
| foundation生成工作负载 | DELETE/WAL各2个固定种子，共4次通过，未观察到结构损坏 |
| foundation前端/参考/构建 | 前端99/11文件，参考29，生产/Demo双构建及所有历史/本轮证据包校验通过 |
| pretrained-encoder | 独立安装PyTorch：9通过、0跳过；官方导出验证完成 |
| browser | 四场景、原开发样片回查、采样、研究协议、历史实验页、A/A.1/A.2及A.3离线页全部通过；216开发静态/8preview、普通文本优化器和双I/O令牌五媒体状态通过 |

基础作业的3项跳过均为`test_numpy_preprocessing_matches_pinned_official_transform`的不同shape/ROI：未安装torch。没有把这些跳过计为通过，也不把有重叠的独立编码器9项与基础作业简单相加。完整原因及新增28项结果可从下方JUnit核对。

本地Python3.12.14/SQLite3.53.1/Chromium154与CI Python3.12.15/SQLite3.45.1/Chrome156均完成对应工程回归；两环境结果分开记录，不视为原独立失败环境已经复现。CI额外4次独立于本地66次主体及2次试运行，全部保留，不更改已发离线Demo的本地66次记录。

- [机器可读作业与跳过记录](ci/summary.json)
- [基础后端JUnit](ci/backend-junit.xml) · [编码器JUnit](ci/encoder-junit.xml)
- [CI四次生成工作负载](ci/workload-results.json) · [浏览器实际JSON](ci/browser)
- [最终本地后端日志](tests/backend-full.log) · [本地JUnit](tests/backend-junit.xml)

完整原始生成trace与日志可从本次Actions的`a3-sqlite-verification`、`browser-verification`及`official-encoder-verification`工件下载。公开提交仅选择JUnit、无敏感标量JSON；没有数据库、partial、特征、权重和视频。Actions自身Node20弃用提示属于运行器注解，未造成作业失败，本轮未为其扩大修改范围。

独立原件复验仍blocked、原2/11根因未确认；CI成功也不代表独立验收通过。
