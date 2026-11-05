# A.4 CI记录

当前最终受测代码为[`5fc70feba7e1168898082f02ffb3b9f4d1cd23df`](https://github.com/qoxgain/jianwei/commit/5fc70feba7e1168898082f02ffb3b9f4d1cd23df)，[GitHub Actions 41964072654](https://github.com/qoxgain/jianwei/actions/runs/41964072654)。三个作业均已完成且成功；本文件依据下载的实际JUnit、14份浏览器主结果和作业日志核对。后续证据提交只改报告/结果，使用[skip ci]避免重跑未改变的代码；受测代码SHA仍为5fc70fe，不将文档HEAD冒充执行版本。

## 已取得的实际证据

| 范围 | 本轮CI实际结果 |
|---|---|
| foundation | 作业通过；后端收集767，764通过、3项缺Torch跳过，0失败/错误 |
| A.4新增 | 57项通过、0跳过（11意图、22准入、17策略、5失败收尾、2预算/取消） |
| pretrained-encoder | 9通过、0跳过；冻结编码器导出核验通过，不发布权重或特征数值包 |
| 生成重复负载 | DELETE/WAL各种子0/1，共4/4通过，最终完整性均ok |
| 小库性能仪表smoke | 16/16通过；不是79/160MiB性能验收或固定旧版对照的替代品 |
| 前端/参考/构建 | 日志核实99项前端、29项参考通过；production/demo双构建通过 |
| browser | 14份主结果均passed；桌面/手机协议、媒体、57项Demo过滤与导出、四场景/播放/采样、216静态及preview回归通过 |

下载[JUnit及结构化CI证据](ci/final-5fc70fe)，[作业/浏览器/哈希汇总](ci/final-5fc70fe/summary.json)。A.4浏览器实际为Chrome 156.0.8078.79，HTML SHA与交付Demo一致，0远程/API请求、0页面异常；此前2个性能失败仍可见，57项新增过滤及JSON精确导出均通过。CI环境Python3.12.16、SQLite3.45.1；本地Python3.12.14、SQLite3.53.1的767通过/0跳过与CI分开报告，不用本地结果覆盖CI跳过。

3项跳过均为`tests.test_pretrained_encoder.test_numpy_preprocessing_matches_pinned_official_transform`的shape0/roi0、shape1/roi1、shape2/roi2：基础依赖未安装torch。已比对testcase名称，三项均在单独安装正式Torch依赖的9项编码器作业中实际通过。9项与主后端有重叠，不能相加宣称776项。精确原因及对应关系见summary.json。

## 保留失败与执行版本

- 候选579bacb的[41958201568](https://github.com/qoxgain/jianwei/actions/runs/41958201568)通过，但早于失败产物收尾修复，不能替代最终CI。
- 候选e7cb801的[41962734839](https://github.com/qoxgain/jianwei/actions/runs/41962734839)失败：后端762通过/3跳过，随后生成负载3/4通过，WAL种子0取消阶段空消息AssertionError；源完整性仍ok。browser与encoder作业通过。完整失败统计保留在[failed-e7cb801](ci/failed-e7cb801)，具体异常码原日志缺失，不反推成已经确认的结构损坏原因。
- 0244e90把DELETE截止与WAL回调取消预算分离，补异常码/traceback，新增2项真实SQLite；新目录12/12重复通过。详见[预算说明](CI-WORKLOAD-BUDGET.md)。没有增加生产busy_timeout或修改提交闸门掩盖失败。
- 本地性能初版核心5c1eb58为142/144通过，中间版55c0ac5为144/144，最终核心4c13aeb为144/144；432个案例与2个初版固定A.3检查超时均保留。最终性能没有在5fc70fe重新执行或改写，核心源文件哈希相同、验证脚本额外补充。
- 本地最终Demo快照代码0244e90，57项新增及767项全量记录；包SHA256为`51f5e551af4c1291ba1aaf1d898f05132867baa01619eb7bca85456e167d6bb4`。仅标量证据、HTML、说明及清单，不含SQLite、原视频、工业特征或权重。

## 尚未解除的限制

file://额外预览被云浏览器管理员策略拒绝，未修改或绕过策略；支持并实际验收的是正常loopback HTTP桌面/手机预览。A.2原媒体/Base64精确反例未重跑，现有安全内容限制不因普通文本优化缓存回归而解除。

两份历史真实损坏副本与独立证据ZIP未取得，精确原件复验blocked。生成数据成功不证明原2/11损坏根因已解决，保持root_cause_not_established。fault_state检测保证变化待独立确认，默认full未改；管理员裸恢复/所有持久介质失效等边界见主报告。B/C/D与正式P0.4均未启动，CI通过也不自动准入。
