# 新来源最小采集与可执行复验

这里只是下一轮开发可行性协议，不是正式封存科研协议。当前真实独立来源执行 **blocked**；不需要动作边界真人标签。不能将本页命令的存在描述为已经取得真实迁移结果。

## 采集及准入清单

1. 同一物理工位、固定相机/视角/分辨率，分别原始录制3个会话，至少跨2个采集日期，每段4—8分钟。每段建议包含至少6次完整重复作业及自然停顿，保留失败、遮挡和无动作区间，不在事后只裁有利动作。6次是采集建议，不是已观察到的周期数。
2. 记录原始文件SHA、根来源SHA、会话/工位/相机/视角身份、带时区采集时间、操作者/班次变化说明和采集日志。原片裁剪、转码、手机转存关系需核对；不同SHA不证明独立。来源/近重复审核人员填写依据，不能只勾verified。
3. 保留可核查的采集日志、根原片链及近重复核查结果（至少对比整片时长、内容和非均匀抽样，必要时按视觉近重复工具人工核实），不要将容器creation_time当采集真值。身份和原片仅放授权私有环境。
4. 使用[空白登记模板](../../plans/p0.3.7-b2u1/source-intake-template.json)。`review_authority`填来源审核责任人及私有证据引用，不是动作标注人。模板中的pending/null必须保持到证据成立；工具只能检查声明的一致性，不能认证现场事实。
5. 三角色fit→calibration→confirmation；全部采集晚于[协议冻结](../../plans/p0.3.7-b2u1/routes-sealed.json)。先仅查看fit来源并锁ROI/全部特征契约、参数和父模型，完成模型冻结回执，再采集另两角色。calibration也仅报告，不据它重新调参；如需更改，显式新协议、重新采集后续确认来源。
6. 背景区域应审核无手/衣物/工件侵入；不存在干净背景时，路线A的背景对照保持未满足，不强行选一个框认证。相机晃动、光照及遮挡作为干扰保留，不凭“框固定”认证画面稳定。
7. 同一生产配方和特征空间；现场只允许原297901…冻结R3D身份。4fps采样敏感性是独立补充实验，另拟合/另冻结父版本，不能将4fps向量或匿名ID混进8fps父模型。没有执行它就不宣称采样鲁棒性。

三段是最低开发可行性样本，不足以宣称跨工厂、跨人群或独立科研泛化。正式封存测试不访问；仍须遵守原协议。

## 立即可运行：缺资料应阻断

在仓库根目录使用已安装依赖的Python。以下输出目录必须从未存在；复跑更换新目录。

```bash
.venv/bin/python -m scripts.validate_b2u1 admit \
  --protocol docs/plans/p0.3.7-b2u1/routes-sealed.json \
  --sources docs/plans/p0.3.7-b2u1/source-intake-template.json \
  --output artifacts/b2u1-review/pending-admission
```

预期退出码2，`ready=false`、`field_transfer_executed=false`。这是准入阻断测试，不是失败的视频迁移实验。

## 获得授权新数据后的真实复验

以下私有路径是待填写的输入约定，当前并不存在这些现场结果。不要用模板/合成来源填写为field来绕过。需先在**授权私有工作副本**中将原始开发来源登记至现有catalog和system.yaml来源注册，split为unassigned；不提交填写后的来源身份或原片到公开GitHub。保留正式test来源登记及根源拒绝规则。

复用现有`prepare_b2u.runtime()`配置构建能力或逐项复制现有隔离运行配置，生成私有fit/calibration/confirmation各自的`config/system.yaml`，不要改根生产配置：

- `local_motion`使用fit来源选择后固定的ROI/显示尺寸、2秒窗口、8fps、1秒步长、明确时间范围及现有PTS容差；各来源SHA/URI单独绑定。
- `unlabeled`算法参数逐项取封存协议的`frozen_unlabeled_settings`，仅`feature_manifest`和`source_evidence`按真实来源填写；不能在query时更改算法或旧复合闸门。
- `source_evidence`填写三来源的根SHA、field、verified/clear依据、capture_session_id、view_id、带时区captured_at，遵守旧来源准入接口。填写的catalog和元数据是声明，不替代采集证据审核。
- 特征文件保留NPZ/JSON于新artifacts目录；不覆盖B.1/B.2-U缓存。资源上限由该隔离system.yaml明确管理，长度/展开预算不足应失败，不静默抽窗或解封test。8分钟约479窗在旧512窗分析上限内；帧数/字节/输出行等接入上限也须按实际内容核实。
- 若仅先检查廉价运动，可关闭所有ROI的`encode_r3d`，深度方法显式unavailable；若计划检验R3D增益，应在看确认来源前固定所有对照和预算，不根据结果选择表示。主判断固定帧差，不将多表示事后最优当主结果。

先提取fit并运行旧算法（不会训练视觉编码器）：

```bash
.venv/bin/python -m scripts.extract_b1 \
  --config artifacts/b2u1-new/fit-runtime/config/system.yaml \
  --output artifacts/b2u1-new/fit-features
OPENBLAS_NUM_THREADS=2 .venv/bin/python -m scripts.run_b2u \
  --config artifacts/b2u1-new/fit-runtime/config/system.yaml \
  --output artifacts/b2u1-new/fit-study
.venv/bin/python -m scripts.validate_b2u1 freeze-model \
  --protocol docs/plans/p0.3.7-b2u1/routes-sealed.json \
  --sources artifacts/b2u1-new/source-intake-private.json \
  --config artifacts/b2u1-new/fit-runtime/config/system.yaml \
  --parent artifacts/b2u1-new/fit-study \
  --output artifacts/b2u1-new/model-freeze
```

此时只需fit来源证据有效，另两角色保留pending；冻结回执绑定4个父文件、主表示fit状态、协议和冻结时间。**随后**采集并审核calibration、confirmation，完善私有登记表，然后执行`admit`（参数换成私有登记表，预期ready=true）。两个来源分别运行`extract_b1`，特征输出目录和配置中的manifest路径一致；父模型不重新拟合。

```bash
OPENBLAS_NUM_THREADS=2 .venv/bin/python -m scripts.validate_b2u1 query \
  --protocol docs/plans/p0.3.7-b2u1/routes-sealed.json \
  --sources artifacts/b2u1-new/source-intake-private.json \
  --model-receipt artifacts/b2u1-new/model-freeze/model-freeze-private.json \
  --config artifacts/b2u1-new/calibration-runtime/config/system.yaml \
  --parent artifacts/b2u1-new/fit-study --role calibration \
  --output artifacts/b2u1-new/calibration-query
```

confirmation使用对应配置、`--role confirmation`及新的输出目录；不能切同一源充当第二角色。query首先核查来源，再读取特征，调用旧冻结查询`update=False`，不追加生产或实验记忆。

```bash
.venv/bin/python -m scripts.validate_b2u1 assess \
  --protocol docs/plans/p0.3.7-b2u1/routes-sealed.json \
  --sources artifacts/b2u1-new/source-intake-private.json \
  --model-receipt artifacts/b2u1-new/model-freeze/model-freeze-private.json \
  --calibration-result artifacts/b2u1-new/calibration-query/session-proxies-private.json \
  --confirmation-result artifacts/b2u1-new/confirmation-query/session-proxies-private.json \
  --output artifacts/b2u1-new/assessment
```

输出分开回答A/B代理是否满足，保留所有方法/种子及负结果；即使代理通过，真实P/R/F1仍null，旧候选不入库、正式P0.4不开放。

## 本轮只读诊断及有限媒体复验

```bash
.venv/bin/python -m scripts.diagnose_b2u1 \
  --report artifacts/p037-b2u/real-study-v3/report-private.json \
  --seal docs/plans/p0.3.7-b2u1/baseline-seal.json \
  --output artifacts/b2u1-review/frozen-diagnosis
OPENBLAS_NUM_THREADS=2 .venv/bin/python -m scripts.stress_b2u1_media_cancel \
  --repeats 6 --model artifacts/p036/models/r3d18-kinetics400-v1.onnx \
  --output artifacts/b2u1-review/media-pressure
```

前者需要授权本地旧数值包，公开汇总不能冒充完整原型包。后者仅生成张量/样片，记录原测试即时FD判定，不改变超时/放宽断言。诊断命令完成不代表每个trial通过，必须读取counts、fd_failures、load状态；未来复现须保留失败并进行小范围清理路径调查，当前未修改媒体运行时代码。
