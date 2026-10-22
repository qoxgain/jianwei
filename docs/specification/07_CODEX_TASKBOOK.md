# 07 Codex实施任务书

> 当前P0.1.1实现边界与3.1契约以 [最终任务书](../plans/P0.1.1-final.md)、[交付记录](../delivery/P0.1.1.md) 和 [P0.2衔接](../P0.2-CONTRACTS.md) 为准；本文其余算法/商业化能力为后续目标。

## 开发原则

本包的Python工具是机制验证参考，不能原样当成多租户生产服务。所有模型适配器以接口隔离；部署参数从system.yaml读取，标签和规范从system.yaml注册的knowledge/scenes/*.yaml读取；labels.yaml仅保留旧版兼容；新增配置字段同时更新schema与说明。训练和检索不允许自行拉取测试集或把unknown转成确定标签。

P0以真实视频或从真实视频导出的特征实现学习闭环。仅完成漂亮大屏、模拟动作或保存日志不能算P0通过。缺少真实视频/模型权重时先完成接口、fixtures和可重复命令，在报告明确尚待现场输入，不编造真实效果。

## 目标代码结构

`apps/api`：API与鉴权；`apps/web`：学习和检测工作台；`workers/capture`、`workers/teacher`、`workers/train`：隔离工作进程；`packages/config`：唯一配置加载器；`packages/contracts`：事件和请求模型；`packages/perception`：编码器/ROI；`packages/temporal`：分段、预测；`packages/memory`：原型/反例/版本；`packages/sop`：规范执行；`packages/evaluation`：分组、指标和门槛；`infra`：迁移及部署生成器；`tests`：契约、数据、因果性和回归；`artifacts`：运行结果。

## P0 自学习技术闭环 第一阶段

| 子任务 | 必做实现 | 交付与完成条件 |
|---|---|---|
| P0.1 配置与数据契约 | 使用严格schema、env密钥解析、配置hash、标签diff；数据库基础表 | 三种环境静态校验；live禁mock；缺配置明确报错 |
| P0.2 真实视频特征管线 | 文件导入、PTS解码、完整周期分组、质量检测、冻结EncoderAdapter、特征缓存 | 真实片段可复现；same hash重复导入不重复处理；提供已有特征NPZ导入 |
| P0.3 匿名时序学习 | 持久性/经验基线、变化点+聚类、ridge和因果TCN或GRU、未来目标和样本屏蔽 | 输入截止可审计；训练无需动作标签；记录训练/校准/测试完整分组 |
| P0.4 语义锚点和候选核验 | 标签文件、标准短视频、固定教师JSON、正反例、人机纠错 | 无预测提示诱导教师；候选与真值分开；无教师也能靠样例映射 |
| P0.5 预测校正与记忆 | 先预测后观察、错误分流、原型/反例/转移/情景记忆；导入导出和版本补丁 | 重复事件幂等；unknown隔离；经验不改SOP；可撤销错误样本影响 |
| P0.6 学生训练与比较 | 自动触发轻量头训练；至少接通TSM或现有动作学生的实际训练适配器；YOLO数据前置检查 | 前后版本同测试集报告；缺框返回skipped；产出真实权重及hash |
| P0.7 评测与最小界面 | 低标注基线/自学习消融、每类指标、预测回放、数据进度、候选确认和模型对照页 | 真值来源明确；展示失败候选；报告标注分钟与覆盖率 |

P0每个子任务可单独提交。P0.6的检测器训练接口及标签校验必须实现；没有可靠框数据时不强求训练出YOLO权重。不要为了满足流程图生成空权重文件或返回success。已存在TSM模型可复用，但需验证输入片段、类别映射和训练代码可重复。

P0最小输入有两种：原始MP4加周期元信息；或冻结编码器导出的特征包。推荐特征包 `features.npz` 包含float32 features[N,D]、int64 pts_start_ms/pts_end_ms[N]、Unicode episode_ids/source_ids[N]，另有feature_manifest.json记录编码器SHA、采样、预处理、维度、source hash与分组。载入NumPy时 `allow_pickle=False`，向量维度及NaN检查；episode内按PTS排序，缺口不配对。

目标命令（待Codex实现，不是本包当前已存在命令）：

```bash
python -m jianwei ingest --config config/system.yaml --manifest inputs/videos.jsonl
python -m jianwei extract --config config/system.yaml --dataset dataset_001
python -m jianwei train --config config/system.yaml --task temporal_head --dataset dataset_001
python -m jianwei evaluate --config config/system.yaml --candidate model_002 --champion model_001
python -m jianwei memory export --config config/system.yaml --snapshot memory_002
python -m jianwei memory apply-patch --config config/system.yaml --patch knowledge/memory_patch.example.yaml
```

## P1 在线视频与业务闭环

接RTSP、MediaMTX播放、环形取证、连续状态与ROI；实现视频60%/状态40%工作台、可配置资源租约与复核；款式锁定、独立返修和评分仅在服装扩展实现。与P0共用特征空间、语义、记忆和版本，不开发第二套线上算法逻辑。事件乱序及迟到用追加修订处理，保留预测原记录。

## P2 自动运行与发布

实现训练调度、资源隔离、可恢复任务、模型注册、自动评估、影子/灰度/回滚、记忆快照激活。已知非关键类可按门槛自动升级，缺乏独立评测或出现漂移自动暂停激活；从界面看到具体阻塞原因。完善权限、统计、历史和配置发布。

## P3 扩场景与深化研究

增加第二个真实工位/行业场景，验证少样本迁移；对比ASOT等时序分段、更强冻结视频编码器、多模态状态特征；再研究区域表征、主动取证及更复杂记忆策略。不将强化学习或VLM训练设为首期依赖。

## 依赖安装与复现

本包requirements只用于工具。正式项目新增PyTorch、FFmpeg、推理框架和前端依赖时，按实际CPU/CUDA平台测试兼容性后提交锁文件、镜像digest、模型revision和权重SHA。不要使用未锁定latest构建验收环境。下载模型由显式开发命令触发，服务启动不静默联网下载大文件。

## 每阶段报告

必须包含：实现文件、可运行命令、输入数据规模/来源、人工标注分钟、训练数据划分hash、权重和配置hash、真实测试结果、失败项、未完成项、下一阶段输入。演示指标放单独部分并标明合成来源；没有真实测试数据就写“未执行”，不能填预期值冒充实测。

## V2迁移

保留已有action_id、设备ID、工序/款式关系和用户权限。先以只读适配器导入旧特征及元信息，核对特征空间，旧512维不保证与新512维兼容。旧配置中的learning和continual_learning合并为单一learning/release，删除双开关歧义。旧V2规范只作迁移参考，V3任务顺序和配置为当前主线。
