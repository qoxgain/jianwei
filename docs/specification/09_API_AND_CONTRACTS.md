# 09 接口与事件契约

> 当前P0.1.1实现边界与3.1契约以 [最终任务书](../plans/P0.1.1-final.md)、[交付记录](../delivery/P0.1.1.md) 和 [P0.2衔接](../P0.2-CONTRACTS.md) 为准；本文其余算法/商业化能力为后续目标。

## 通用协议

HTTP JSON，`/api/v1`；OpenAPI3.1核心契约见 `contracts/openapi.yaml`。所有写请求携带Idempotency-Key及鉴权上下文；tenant由服务端验证。长任务202返回job_id/status_url。冲突409、结构错误422、缺权限403、依赖未就绪503。错误体含code/message/trace_id，不返回凭证、任意文件路径或堆栈。

| 方法与路径 | 请求关键内容 | 结果 |
|---|---|---|
| POST /learning/ingestions | 已注册asset_ids、scene、split、group元信息 | 异步导入任务 |
| POST /learning/jobs | dataset_version、task、config_hash | 特征/分段/训练/评估任务 |
| GET /jobs/{job_id} | 任务ID | 状态、进度、失败原因、输出引用 |
| GET /learning/clusters | scene、knowledge_version、分页 | 匿名簇及代表样本 |
| POST /knowledge/validate | knowledge JSON、base_version | 错误与diff，不写生产 |
| POST /knowledge/releases | validated_revision、base_version | 发布任务，绑定审计 |
| GET /memory/snapshots/{version} | 版本 | 记忆摘要、样本来源和兼容特征空间 |
| POST /memory/patches | base_memory_version、base_knowledge_version、operations | 候选新版本与影响集合 |
| POST /observations/{id}/feedback | decision、correct_action、evidence、理由 | 追加标签修订与依赖失效 |
| GET /predictions | episode、issued_after、分页 | 原预测及后来观测，二者分开 |
| POST /evaluations/compare | candidate、champion、dataset、protocol | 异步同集对比 |
| POST /models/releases | candidate、evaluation、policy、expected_current | 影子/灰度任务，不绕过门槛 |
| POST /models/rollback | release、target_compatible_tuple、reason | 原子回滚并审计 |

业务基础接口仍需要：health/live、health/ready、runtime/config（仅前端白名单字段）、cameras/streams、sessions/start/end/rework、events/evidence/review、statistics、users/roles/departments、styles/processes。详细行为见产品和界面章节。不要把P0核心OpenAPI误认为全部商业化管理接口已经实现。

## 预测事件

`prediction.created`含event_id、tenant_id、scene_id、source_id、episode_id、entity_id、seq、issued_at、input_max_pts_ms、target_start/end_pts_ms、topk或概率分布、prediction_kind、model_version、memory_version、knowledge_version、data_origin。事件ID稳定且写入后不可覆盖。

`observation.verified`只记录独立证据、动作和质量层级。`prediction.scored`引用两个已存在ID，记录是否可评分及理由；发生时间在结果可获得之后。`memory.candidate_built`、`memory.activated`、`training.started/completed/skipped`、`evaluation.completed`、`release.changed`、`state.corrected`使用相同事件外壳。

WebSocket `/ws/v1/scenes/{scene_id}`先发runtime.snapshot，之后seq递增。断开后带last_seq恢复；超出保留窗口发完整快照。与视频PTS对齐，不按消息先到后到决定工序顺序。

## 教师输出

教师只输出 `schemas/teacher_result.schema.json`支持的JSON。字段包括observed_action_id或null、time_range_ms、evidence_frame_ids、visible_cues、uncertainty、support_level和alternative_action_ids。不要求或信任教师自报精确概率；服务端检查动作在候选集合、时间在输入窗、帧ID存在、没有超出权限的引用。

输出只进入candidate。只有规则验证器与独立抽检可升级质量；教师不得调用模型发布、SOP修改或任意系统命令。提示词见 `prompts/teacher_observation.txt`。

## 编码器和训练适配器接口

`EncoderAdapter.encode(ClipBatch)->FeatureBatch`；`Segmenter.segment(FeatureSequence)->SegmentProposal[]`；`Forecaster.predict(History,cutoff)->Forecast`；`Verifier.verify(Observation,Evidence)->QualityDecision`；`MemoryBuilder.build(SampleManifest,BaseSnapshot)->CandidateSnapshot`；`TrainAdapter.capabilities()/validate_dataset()/train()->TrainingArtifact`；`Evaluator.compare(candidate,champion,EvaluationManifest)->EvaluationReport`。

接口返回结构化artifact URI与hash，不返回只有自由文本的“训练成功”。特征/模型下载执行路径来自登记资源，禁止任意URL与文件路径执行。

## 数据清单

每行至少包含sample_id、source_video_id、source_hash、episode_id、split、start_ms、end_ms、data_origin。生产扩展tenant、scene、source、entity、context、feature_space、quality_tier、evidence_id、annotation_revision及box_verified。`examples/dataset_manifest.jsonl`仅为合成格式示例；校验工具拒绝source/episode/hash跨分区。

评估比较双方必须有相同dataset_hash和protocol_id，明确每类样本数、置信区间、coverage、误报率及延迟。参考compare_versions只实现数值门槛，不实现真实模型测量、bootstrap或部署；这些需按任务书补全。
