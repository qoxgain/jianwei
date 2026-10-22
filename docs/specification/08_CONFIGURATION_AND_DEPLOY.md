# 08 配置和部署调试

> 当前P0.1.1实现边界与3.1契约以 [最终任务书](../plans/P0.1.1-final.md)、[交付记录](../delivery/P0.1.1.md) 和 [P0.2衔接](../P0.2-CONTRACTS.md) 为准；本文其余算法/商业化能力为后续目标。

## 唯一配置入口

所有运行参数以 `config/system.yaml`为事实来源。profile不是多份独立配置，而是同一文件中的环境块。路径解析固定以配置文件所在目录的上一级作为项目根；各相对路径与当前shell工作目录无关。`paths`子目录相对选中profile的data_root；模型从model_root管理，具体encoder_weights为显式路径。

加载顺序：YAML重复键检查 → schema → profile选择 → env引用解析 → 语义交叉检查 → 路径解析 → 依赖预检 → 脱敏运行快照。CLI只允许选择config/profile、业务任务或输入manifest，禁止维护另一套同名默认超参。程序新增阈值必须放配置中，常量只允许数学常数和协议枚举。

`env://JIANWEI_CAMERA_01_RTSP`的环境值包含完整摄像头地址；特殊字符需正确URL编码，日志只记相机ID。数据库口令、教师API Key、JWT Key也由env引用取得。环境变量名本身存于唯一YAML，无需再维护含业务参数的.env文件。

## 环境切换

| 场景 | 修改位置 | 预期 |
|---|---|---|
| 无GPU机制验证 | active_profile=demo | 合成特征示例可运行，明确演示来源 |
| 本地录像研究 | active_profile=lab，source.uri、encoder_weights、device | 检查视频和权重，导入真实数据；教师可关闭 |
| 客户生产 | active_profile=site，IP/域名/目录、数据库和相机env | auth开启，正式标签，模型SHA锁定，禁止模拟回退 |
| 新工序 | 对应场景知识包新增draft及样例 | 候选注册、核验与独立评估 |
| 新行业 | knowledge_file指向新知识包、active_scene及source.scene_id同步 | 要验证模型适配和成像能力，不承诺改YAML就获得新视觉能力 |

所有相机的ROI采用原始帧归一化xyxy。单工位模板迁到新角度必须重新检查ROI和特征分布。前端WebRTC/HLS地址来自同一profile的media块，部署生成器负责反向代理配置；不能手改生成的MediaMTX文件导致配置分叉。

## 当前命令与未来命令

本包可运行的三个命令见README。`config_doctor --preflight`仅检查本地权重、输入文件、env及正式知识状态，不探测网络、GPU和相机；demo无需这些资产。lab/site示例预检失败是预期的待配置状态，不应通过填假路径或跳过检查消除。

正式Codex需要新增 `doctor --runtime` 检查DB、Redis、特征库、模型加载、显存、FFmpeg解码、教师schema和流播放，以及 `render-deploy` 将YAML编译为Compose/服务/媒体配置。生成物携带config_hash，不允许人工修改后继续显示与YAML一致。

## 热更新

UI展示、候选采样阈值等可生成新配置快照并用于新任务；IP、模型权重、数据库、采样率、特征空间、知识规则和密钥变更需要对应重启/重建/复评。更改采样率会改变时序分布，不能简单热换并沿用原模型指标。

训练期间冻结数据快照和配置；用户修改文件不影响已运行训练。训练结果携带旧config_hash，发布器必须判断是否仍与当前运行环境兼容。

## 超时和故障定位

特征提取、批量入库及训练使用异步job，HTTP提交应快速返回202；客户端按job查询最终状态。不要通过无限延长Gunicorn或客户端超时来代替任务机制。教师60秒超时为研发初值，可独立调整；关闭教师不影响基本视频和原型检索。

按trace_id→job_id→episode→evidence→feature_space→memory/model版本查链。重点错误包括VIDEO_OFFLINE、DECODE_GAP、FEATURE_SPACE_MISMATCH、NO_SEMANTIC_ANCHOR、LABEL_CONFLICT、GOLD_INSUFFICIENT、BOX_LABELS_MISSING、TRAIN_BUDGET_EXCEEDED、REGRESSION_GATE_FAILED、CONFIG_RESTART_REQUIRED。

## 首次现场检查

确认真实相机拍到关键手部/部件，至少一个完整动作可见；准备合法的独立评测片段；以只读模式接入已有系统，记录原champion；回放真实视频跑通B0，再跑记忆和时序增量；最后启用在线学习候选收集。磁盘、网络和GPU容量用实测吞吐决定，不在文档中许诺固定硬件能支撑任意路数。
