# AUD-02 依赖闭包补全（实际实验之后）

已实现、已测试：配方升级为 `p036-feature-production-v2`，追加 canonical hash 的 `packages/knowledge` 和配置迁移的 `packages/migrations` 源文件。修改这两个间接执行依赖也会使生产配方、缓存复用键及正式研究代码版本失效。新增两项变更注入回归，相关 63 项通过（本地日志 `artifacts/p036/dependency-followup.log`）。

P0.3.6 的九视频重新提取和所有数值实验均在此补全之前执行，原清单保留 `p036-feature-production-v1`，没有改写为 v2。补查两个目录的全部四个文件，与实际数值代码提交 `2694a9504015f15569c9d18568b20330441963c7` 完全相同；本地证据 `artifacts/p036/transitive-helper-provenance.json`。这些实验只作回顾性探索，不能绕过最新正式协议的 v2 配方检查。

新任务采用 v2 生成新特征空间；历史缓存和模型继续可读，只有配方一致才可复用。未因补全元数据修改原特征、权重、报告或研究门槛；未针对留出结果调整算法。
