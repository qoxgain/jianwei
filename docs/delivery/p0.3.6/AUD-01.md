# AUD-01：来源与周期覆盖

基线 0bf8fba；独立分支 codex/p0-3-6-reliability-temporal-20261103。不合并探索分支。

窗口预算先满足每个有效来源及其每个有效 episode/时间域/实体序列至少一个窗口，再按来源进行 max-min 公平分配，来源内继续按序列公平分配并均匀选取时间点。正式准入已保证一份来源对应一个独立会话，因此不会把同会话的多个文件重复加权。预算小于必要序列数时返回 TEMPORAL_WINDOW_BUDGET，不截掉尾部来源。请求中任何来源完全没有合法窗口时，完整覆盖检查返回 TEMPORAL_SOURCE_COVERAGE。

训练/评估排队前及实际执行时重新检查元数据、资源和全部来源，正式评估在预留及消费之前完成检查，不读取向量。原 window_refs 的双返回值兼容；新增可选 coverage 输出与报告 window_sampling，记录来源覆盖率、各周期有效/选中/预算省略窗口、时间/质量排除原因。

实际回归：88 passed（来源/多周期、预算不足、消费前阻断、原训练/评估/准入/采样）；前端 93 passed、生产构建成功。未运行真实学习，未读取正式封存特征。此前抽样反例已经变为预期明确阻断；这不代表真实研究门槛通过。

新测试 tests/test_temporal_source_sampling.py 覆盖 1/2/8/9/25 来源、9来源8预算、多周期来源公平性、逆序输入一致、无窗口来源和正式预留前失败。相关修改位于 temporal/{data,sampling,engine}.py 与 temporal_jobs/{service,readiness}.py。
