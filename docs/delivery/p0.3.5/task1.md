# 子任务 1：冻结协议、独立会话统计和封存保护

基线 4a0f7bb；沿用原分支和已有模块，不改写原始媒体、知识或历史记录。

实现严格研究协议配置与只读 API、训练前完整分区/版本冻结、实际特征配方匹配、会话等权配对 bootstrap 和双对照门槛。真实 test 按租户及源/根/会话预留，首次特征读取前持久化消费记录，失败/重启后禁止重读。历史训练、校准或评估资料不能经转码重新成为封存测试。未固定候选时不检查测试窗口，前端兼容空值并显示“未检查”。

相关文件：packages/contracts/research.py、packages/config/loader.py、packages/temporal/{protocol,uncertainty,data,engine}.py、packages/temporal_jobs/{service,readiness}.py、apps/api/main.py、协议 schema/OpenAPI，以及对应测试和只读元数据审计脚本。原有接口与旧报告保留；新增的 test 窗口空值是封存保护的明确状态，旧数值报告仍可读。

验证：全量后端 516 passed（无跳过），协议专项 44 passed；参考实现 29 passed。前端 93 passed，生产和离线构建均成功。完整性检查 10 个原始文件通过（只核对字节，本次没有重解码全部视频）；迁移检查从 0003 到 0005，旧字段保留、重复执行一致，无新增数据库迁移。

实际命令：python -m pytest tests -q；python -m pytest tests/test_research_protocol.py -q；python -m unittest discover -s reference/tests -v；npm --prefix apps/web test；npm --prefix apps/web run build；npm --prefix apps/web run build:demo。原始结果见本目录日志。

真实多会话效果 blocked：没有新增固定机位独立录像及完整审核记录。合成机制用例只验证代码，不作为真实效果通过证据。后续子任务完成页面交互、离线 Demo 和交付汇总。
