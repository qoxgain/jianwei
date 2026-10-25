# P0.2 本地数据接入

运行 `bash scripts/install.sh`、`bash scripts/start.sh` 后，在本机浏览器打开应用，进入“数据接入”。这是回环地址上的单用户服务；lab/site 生产鉴权与 RTSP 在线服务仍未开放。

## 没有数据时

选任意场景，点击“运行示例导入”。服务端生成 120 行确定性合成特征，并走相同的上传登记、队列、校验、缓存及 SQLite 提交流程。重复点击复用相同任务；刷新后任务仍存在。四场景各有独立上下文与周期清单。

示例编码器文件是解析生成公式的 JSON 指纹，**不是预训练视觉模型**。没有采集时钟，因此 UTC 保持 unknown。示例默认未分配，不进行训练。独立 HTML 只展示这些实际生成的静态清单，不能上传文件或运行服务端任务。

导出可检查的示例输入：

```bash
.venv/bin/python -m scripts.make_ingestion_example \
  --scene anonymous_observation --output-dir artifacts/input-example
```

## MP4

选择 `file_01`，上传 MP4，填写场景必填上下文。服装需相机和款式，电子装配需工位和工单；安全及无标签观察不强制行业字段。动作标签与 SOP 不是导入前提。

手工/外部周期以原始 PTS 毫秒给出；结束必须晚于开始，不能重叠或越过时间域。电子装配要求 external 来源；固定窗口场景由配置生成周期，末尾不足一窗会标记不完整。只有完全落入完整周期的合格片段进入 FeatureBatch。

```json
[{"episode_id":"cycle_1","domain_index":0,"start_ms":0,"end_ms":60000}]
```

服务使用 FFprobe 读原始帧 PTS，再用 FFmpeg 实际解码；只接受本地文件协议，限制时长、像素、帧数、展开体积与处理时间。清单记录亮度/对比度、缺帧和被拒片段；这些指标不是识别准确率。当前默认编码器为 `existing_features`：MP4 解码后会明确 blocked，保留清单，不生成假特征。配置真实冻结 ONNX 模型见 [ENCODER.md](ENCODER.md)。

PTS 重置及超过配置容忍阈值的缺口创建新时间域。采集 UTC 不从文件时间猜测；需要时提供锚点，每个重置域独立给出：

```json
[{"domain_index":0,"pts_ms":0,"capture_utc":"2026-10-23T08:00:00+08:00"}]
```

## NPZ

输入只能包含三个 C-order 数组，使用 `np.savez_compressed` 导出：

| 字段 | 约束 |
|---|---|
| `features` | float32/float64，二维 `[N,D]`，非空且有限 |
| `pts_ms` | 数值一维 `[N]`，非负整数毫秒；不排序消除重置 |
| `durations_ms` | 数值一维 `[N]`，正整数毫秒 |

拒绝 pickle/object、复合类型、重复 ZIP 成员、额外字段、异常数组头、维度/行数不一致、NaN/Inf 以及超过限制的展开内容。

同时填写 FeatureSpace：`contract_version=3.1`、`encoder_sha`、`preprocessing_sha`、`sampling_sha`、`roi_policy_hash`、`pooling`、`dimension`。所有 SHA 为小写 SHA-256；前端可用实际上传的编码器指纹文件计算 `encoder_sha`。ROI hash 是 `{"source_id":...,"roi_xyxy":[...]}` 的 RFC8785 规范化 JSON SHA。页面提供元数据模板。

指纹上传只做文件哈希，绝不执行上传的模型。指纹匹配不能证明这些外部向量确由该编码器产生，清单保留 `externally_declared`。上传原 MP4 后，可核验源字节、实际解码与时间范围；未提供原 MP4 时必须声明 `declared_source_hash`，来源保持 `declared`，只能是 `unassigned`。

原视频有重复 PTS 时间域时，HTTP 请求可额外提供 `source_domain_map`：按 NPZ 本地域顺序给出对应原视频域索引；存在歧义而未提供映射时拒绝导入。此高级映射目前通过 API 提交。

## HTTP 与持久性

| 接口 | 行为 |
|---|---|
| `POST /api/uploads?kind=mp4\|npz\|encoder` | 请求体为原始文件字节；返回 ID、实际 SHA、大小、用途 |
| `GET /api/ingestion/capabilities` | 当前限制与编码器状态 |
| `POST /api/scenes/{scene}/imports` | 提交 [ImportRequest](../schemas/import_request.schema.json)，返回 202 和任务 |
| `POST /api/scenes/{scene}/imports/example` | demo 专用，运行随附合成示例 |
| `GET /api/scenes/{scene}/imports` | 最近 200 条任务摘要，不重复载入全部大清单 |
| `GET /api/scenes/{scene}/imports/{id}` | 单任务状态、错误和完整清单 |
| `GET /api/scenes/{scene}/imports/{id}/manifest` | 导出已生成清单 |
| `POST /api/scenes/{scene}/imports/{id}/retry` | 同一配置下重试 blocked/failed 任务 |

任务状态为 queued → running → succeeded / blocked / failed。队列写入数据库后才返回；重启恢复未完成任务。配置或知识基线变更时旧任务阻塞，需新建导入，历史记录保留。错误仅返回可诊断代码和说明，不返回服务器路径。

幂等键绑定租户、场景、输入内容、请求、配置及知识哈希。同租户、同原始视频跨场景/ROI 共享分组与已确定分区；请求冲突会失败。未核验来源不会继承已分配的训练组。后续训练器还必须核对完整周期、质量、来源验证和特征空间，不能只读取向量文件。

配置上限位于 `system.yaml.ingestion`。默认上传 64 MiB、展开/活动缓冲预算 128 MiB、时长 600 秒。P0.3.1 视频用完整逐帧质量扫描加有界采样缓存，不再把整段 RGB 写入临时磁盘；预算约束单帧、活动片段及张量，不代表包含 ONNX 权重的整个进程 RSS 上限。NPZ 仍按完整展开体积检查。帧数、分辨率、时长和处理超时限制保留。数据留在配置的 inbox/features/datasets 路径；当前无删除、配额管理、分布式队列或生产权限功能。

核验后的域身份保留原视频域和本次连续片段范围，防止独立 NPZ 都叫 d0 时错误配对。不同 NPZ 连续段、MP4 与独立 NPZ 不自动跨段做未来配对；后续须显式重建连续性。
