# P0.3.1 采集来源与分区

真实研究需要在 `system.yaml` 的 `temporal.dataset_manifest` 中登记完整来源清单。文件 SHA 只证明字节身份；不同文件名、截片、转码、多机位同步录像不能自动计为独立采集会话。匿名单视频预览仅接受 `unassigned`，不通过训练入口读取封存数据。

[test-source-dataset.yaml](../examples/test-source-dataset.yaml) 是已上传视频的待核验清单。只登记实际 SHA，采集日期、机位和会话未知，审查保持 `pending`，不编造现场信息。把该文件的顶层内容置于 `temporal.dataset_manifest`，再补真实采集记录。

每个来源包含 `source_sha256`、`root_source_sha256`、`parent_source_sha256`、`relation`、`capture_session_id`、`camera_id`、`view_id`、带时区的 `captured_at`、固定 `split`。`view_id` 使用实际导入清单的视角摘要。原片根指向自身；派生文件必须登记父节点和原始根，并继承采集信息。同源和同会话全局只能属于同一分区。

`provenance_review` 与 `near_duplicate_review` 均需审查人、时间、方法和证据说明才可置为 `confirmed`。近重复审查应结合原始采集记录、时长/时钟和代表帧人工对照，检查剪辑、转码及重叠拍摄。这里实现的是人工审查记录与阻断门槛，尚未实现自动近重复视频检索。可疑文件对登记在 `duplicate_reviews`；待处理项会阻断相关整个采集会话。确认 `same_session` 后还必须将来源归入同一会话和分区；不能仅靠一条备注消除泄漏。

训练前固定整个 train/calibration/test 清单。请求中的导入须匹配租户、视角、来源和分区，所有父来源也须完成审查。同会话多个导入被拒绝，不能增加独立来源计数。训练/评估快照保留完整清单 hash 和谱系；评估再次检查会话、原始根及字节重叠。训练后改动清单会阻断原候选的评估，须重新固定方案。

最小通路为 2 个 train、1 个 calibration、1 个 test 独立采集来源；这只是软件门槛。应覆盖多日期/人员并提前确定评估方案，不能据四个来源声称泛化充分。查看封存结果后继续针对结果修改方案，需要新的封存测试集。

本阶段没有新增数据库表。已有来源字节分区、导入记录不改写；旧配置不填该可选清单时 hash 不变。历史真实研究在缺少采集记录时不能按新门槛再次执行；合成示例保持可运行。实际多源验收仍等待用户提供独立录像及采集记录。
