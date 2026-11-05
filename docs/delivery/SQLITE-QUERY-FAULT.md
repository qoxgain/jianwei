# SQLite查询阶段异常处理专项修复报告

本轮仅处理A.4明确复现的查询/取行异常遗漏，继续使用`codex/p0-3-7-local-motion-learning`。固定代码基线5fc70feba7e1168898082f02ffb3b9f4d1cd23df；实际起始HEAD c8eb606a329b5fcd7269499360f20c82cf93f7e4与远端一致、工作区原本干净。二者相关生产源码字节相同，校验见[provenance.json](sqlite-query-fault/provenance.json)。本轮不重新设计数据库架构，不修改备份生命周期、故障意图或媒体保护，不合并正式分支、不启动B/C/D或正式P0.4。

用户已确认两份原始损坏库和原11次脚本本轮无法提供，其精确复验保持blocked，不编造路径、SHA或复验结果。所有本轮损坏均来自新建生成SQLite库，不公开数据库或数值包。原持久损坏产生原因仍为root_cause_not_established。

## 反例与原因

[整改前32组记录](sqlite-query-fault/before.json)实际使用Python3.12.14/SQLite3.53.1。组合为full/fault_state、DELETE/WAL、execute/executemany/fetchone/fetchmany/fetchall/next/SQLAlchemy Result/ORM八条路径。

| 基线查询路径 | 返回真正SQLite损坏错误后 | 后续写入 |
|---|---|---|
| execute/executemany，两个策略 | 已有原生执行异常记录，会隔离 | 拒绝 |
| 四类直接取行，full | 没有立即隔离 | 提交前full检查另行发现并阻断 |
| 四类直接取行，fault_state | 没有立即隔离 | DELETE/WAL合计8次提交，独立连接各读到1行 |
| SQLAlchemy Result/ORM，两个策略 | 原有handle_error监听已能隔离 | 拒绝 |

原因是ManagedCursor委托GuardedCursor，但后者只覆写执行方法；原生fetch/next失败没有进入故障记录。SQLAlchemy能另行捕获部分结果消费错误，不能替代直接DBAPI路径的保护。

[整改后相同32组](sqlite-query-fault/after.json)均在查询异常后形成持久意图/隔离，后续非法提交0，独立读取新增行0。没有把full在提交时补检冒称为基线已经具备统一查询隔离。

## 定向代码修改

- [sqlite_guard.py](../../infra/sqlite_guard.py)中的`sqlite_operation`是唯一原生调用异常入口。execute、executemany、fetchone/many/all、next及受支持迭代均委托它；COMMIT、ROLLBACK、属性隐式提交和内部BEGIN亦复用。ManagedCursor保持原来的返回对象/工厂及SQLAlchemy适配，不再另加一层相同except。
- 入口只接直接绑定的sqlite3原生方法。SQLite错误从该原生调用直接返回，与行/文本工厂、参数迭代器中的Python回调错误区分；后者的下层Python帧标记其应用来源，SQLAlchemy后续包装不把另一个库的错误归给当前库。依赖受测CPython的异常traceback行为，不能把任意第三方C扩展适配器当成已经验证过。
- [database.py](../../infra/database.py)中的`Database._record_sqlite_fault`要求sqlite3.Error及真正数字结果码，按`code & 255`支持扩展码；CORRUPT/NOTADB记结构损坏，既有IOERR/FULL记存储故障。BUSY/LOCKED/INTERRUPT、业务异常、普通超时不永久隔离。仅携带相似名称的普通对象不会被视为驱动故障。
- 每个异常按Database观察令牌去重，SQLAlchemy再次接到同一原始异常不会重复写意图或覆盖首份故障证据。已有隔离不重写。
- 隔离等待/存储失败不会覆盖原始DBAPI类型、code或对象：原错误保留，附加明确note并通过cause保留DATABASE_ISOLATION_PENDING/PERSISTENCE_FAILED。并未忽略隔离失败后恢复健康写入。

新代码不打开另一个等待SQLite写锁的连接。原生调用返回后才进入原有fault setter；setter先持久化停止意图，再有界等待提交闸门。已持有闸门时复用现有租约，不重复取得文件锁。未获提交准入的在途写事务拒绝/回滚；更早已经获准进入native COMMIT者仍遵循A.4原先的排空语义。本轮没有改变这一线性化边界。

## 生成夹具与测试

复用`tests/sqlite_corruption_fixture.py`，保留原freelist计数夹具和原调查脚本。旧freelist夹具常返回完整性错误行，不保证在取行时抛异常；因此在同模块追加生成叶页/文件头变体。新`tests/sqlite_query_fixture.py`先创建健康业务库，准备已通过完整检查的连接，再由spawn子进程修改新建生成库的后续叶页。主SQLite进程不裸读/关闭源DB普通FD，保留A.3取证锁保护。查询成功准备后跨到坏叶页时，SQLite真实返回CORRUPT；文件头变体真实返回NOTADB。测试不接收原始库路径。

新增100项：32条损坏路径矩阵、4条spawn/两次重启、36条业务回调异常、SQLAlchemy回调及业务事件、Row/arraysize/迭代兼容、真实BUSY/LOCKED/INTERRUPT、已持闸门、闸门超时与持久化失败、NOTADB和直接GuardedCursor迭代。8项扩展码分类是明确的合成分类单元测试，不冒称真的发生磁盘IOERR或空间耗尽。其余测试使用真实SQLite连接/生成数据，部分存储失败由明确故障注入模拟。

138项专项及原保存点/备份/事务回归通过。[相关JUnit](sqlite-query-fault/related-junit.xml)、[完整日志](sqlite-query-fault/related.log)。32条矩阵逐项检查真实SQLite错误码、持久意图、另一在途连接COMMIT、新Session、新Database连接和备份发布拒绝，以及独立只读计数0；4条spawn用例再执行两次进程重启。回调覆盖普通ValueError、TimeoutError、取消、无码sqlite3.Error、伪造码以及来自另一个生成库的真实NOTADB，均保持原对象且不隔离健康当前库。

既有3份事务/备份测试中，过去以SimpleNamespace假装SQLite驱动IOERR的辅助注入改为显式`db.fault`测试注入；原断言、保存点变体和原生备份目标测试全部保留。它们测试已知故障后的闸门保护，不再伪装成真正驱动异常来源。

## 保留失败与未验证范围

- 初次生成夹具试运行，execute查询从固定rowid开始没有立即命中坏页，断言失败；改用聚合强制执行期扫描，日志见[fixture-pilot.log](sqlite-query-fault/fixture-pilot.log)。该次不是产品修复成功证据。
- 首次扩展回归134通过/2失败：屏障先占闸门，再试图准备查询，查询在观察损坏前合法返回协调超时。改为先准备只读游标，再占闸门，在fetch时观察真实CORRUPT；最终138通过。初始日志保留[related-initial.log](sqlite-query-fault/related-initial.log)，未删除用例或降低断言。
- 原始实际损坏样本及原11次脚本未复验：blocked（用户已明确本轮无法提供）。生成夹具证明异常传播缺陷及修复，不证明原结构损坏产生原因已查明。
- 第三方原生C扩展适配器、其他Python实现、断电/不可靠文件系统未验证；不把Python回调测试外推成任意本机代码的隔离保证。

## 完整回归与CI

本地全量后端867通过、0跳过，新增专项100项；前端99、参考29及production/demo双构建通过，见[本地汇总](sqlite-query-fault/local-results.json)与[后端JUnit](sqlite-query-fault/backend-junit.xml)。两项旧弃用警告保留：Starlette BlockingPortal别名及原fork连接隔离用例。

修复代码提交为`8d7fbe7a9e7699df531b620f183fe45643186e5f`，对应[CI 41989486262](https://github.com/qoxgain/jianwei/actions/runs/41989486262)三个作业均成功。基础后端864通过、3项缺Torch跳过，新增100项全部通过；单独正式依赖编码器9通过/0跳过，实际覆盖这3项但不与后端重复累加。前端99、参考29、双构建及14份浏览器主结果通过。详细[CI证据](sqlite-query-fault/ci/summary.json)与[CI JUnit](sqlite-query-fault/ci/backend-junit.xml)已下载核对。本地前后32组是在c8eb606父提交的未提交工作树完成，实际生产源码哈希见provenance.json；后续CI执行明确的固定基线与修复提交，不能把文档HEAD当执行版本。生成API/并发/在线备份/异常退出负载DELETE/WAL各两次，共4/4通过，完整性均ok，详见[workload.json](sqlite-query-fault/workload.json)。CI已使用完全相同生成夹具，在固定5fc70fe应用源码及修复源码上分别运行32组，保存源码SHA、Python/SQLite版本与全部结果；基线只叠加生成测试支持文件，不改infra/packages。

本轮没有改动或重打包历史A.4 Demo、报告、性能矩阵、工业视频、研究特征、权重或封存记录。默认full不变；fault_state在SQLite尚未返回损坏错误且巡检尚未发现时，仍有原来明确的检测盲区，不声称等同每次full扫描。

## 两套SQLite实测对照

| 执行环境 | 固定A.4/等价基线 | 修复后 | 来源 |
|---|---|---|---|
| 本地Python3.12.14 / SQLite3.53.1 | 查询后隔离16/32，非法提交8、独立读取8行 | 查询后隔离32/32，非法提交0、独立新增0行 | 本轮生成叶页夹具 |
| CI Python3.12.15 / SQLite3.45.1 | 查询后隔离16/32，非法提交8、独立读取8行 | 查询后隔离32/32，非法提交0、独立新增0行 | 完全相同生成支持文件，应用核心SHA逐文件核对 |
| 两份独立原始损坏库 | 本轮无法提供 | blocked，未执行 | 用户已确认，不用生成夹具冒充 |

CI基线和修复版细节分别为[before.json](sqlite-query-fault/ci/before.json)、[after.json](sqlite-query-fault/ci/after.json)。修复版真实SQLite异常返回后立即调用既有隔离机制；查询未观察到任何损坏时的检测能力差异仍保留。现有生成证据满足本轮受测CPython/sqlite3和列明路径的工程成功标准，具备独立复验条件；不宣称原件验收通过或原损坏根因已解决。

后续证据提交仅追加本报告/脱敏结果并使用[skip ci]，不修改已通过的生产源码；受测代码SHA仍为8d7fbe7，不用证据HEAD冒充执行代码。

## 独立复验

```bash
.venv/bin/python -m pytest tests/test_sqlite_query_fault.py tests/test_p037_a4_*.py tests/test_p037_a3_*.py tests/test_p037_a2_backup.py tests/test_p037_a1_savepoints.py tests/test_p037_a1_backup.py tests/test_p037_transaction_gate.py tests/test_sqlite_reliability.py tests/test_research_protocol.py -q
.venv/bin/python -m scripts.reproduce_sqlite_query_fault --output artifacts/query-independent-new
```

命令只接收新输出目录，不接收待修改的原始数据库。固定5fc70fe对照见CI工作流：新建独立worktree，叠加同版生成测试支持文件，验证infra/packages未变，再加`--expect-vulnerable`；这表示预期复现漏洞，不表示旧代码安全通过。

完成本专项后停止数据库架构修改，提交独立复验；不自动开放P0.3.7-B或正式P0.4。
