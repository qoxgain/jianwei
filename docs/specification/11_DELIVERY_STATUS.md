# 11 本包验证与交付状态

> 本文件原始内容为研发交接包状态快照。当前代码实现与实际测试结果请以 [P0.1.1交付记录](../delivery/P0.1.1.md) 为准；真实视频与学习效果仍未验证。

本文件记录交接包本身的验证，真实视频算法验收尚未执行。

## 可运行工具

| 工具 | 实际能力 | 限制 |
|---|---|---|
| config_doctor.py | 严格YAML/schema、标签引用、流程环路、环境参数及本地先决条件 | 不测试网络、相机、GPU和模型服务 |
| self_learning_demo.py | 合成冻结特征、ridge预测头训练、经验转移校正、SQLite候选记忆及预测日志 | 不包含视频解码、语义标注、生产激活 |
| learning_core.py | 记忆去重/隔离/余弦检索、分组泄漏检查、数值版本门槛 | 单进程参考；单中心原型；不含完整质量核验和自动发布 |
| build_contracts.py | 从Pydantic生成配置、标签、补丁、教师和预测schema及核心OpenAPI | 不是API实现 |
| validate_manifest.py | 检查样本ID、时间窗、source/episode/hash跨分区泄漏 | 不替代真实视频近重复检测和完整审计 |

合成实验和检查的实际输出见 `verification/toolkit_verification.json`、`verification/demo_report.json` 和 `verification/unit_tests.txt`。测试数据、模型权重均是合成小例子的结果；只用于确认机制及复现，不可放到客户宣传材料中当作识别准确率。

首个真实试点验收必须另行提供：原始视频/编码器或已有特征、正式SOP、少量语义样例、独立评测真值、现有基线、资源实测。按照任务书完成这些输入后才能评价真实自学习收益。
