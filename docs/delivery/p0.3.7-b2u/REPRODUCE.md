# B.2-U 使用与独立复验

本轮由用户授权改变路线：零标签研究不需要动作真人标注。保留B.1.1盲标工具，但不要求先标注。公开包只含实际合成曲线、代码和工业汇总；工业视频、逐窗口轨迹、特征和原型数值均留在本机。真实P/R/F1=null，正式P0.4 blocked。

## 查看 Demo

解压 Demo ZIP，在该目录执行 `python -m http.server 18073 --bind 127.0.0.1`，浏览器打开 `http://127.0.0.1:18073/index.html`，选择“无标注模式研究”。这是本机查看地址，不是公网托管站点。

默认是两个分别生成的64秒来源的真实执行报告；合成仅执行B.1已有帧差/块运动，未执行R3D。上方11种工业表示汇总来自真实服装开发缓存，不能将其和下方合成曲线混为一谈。

合成视频回查不需要模型：安装numpy、FFmpeg后执行 `python generate_b2u_practice.py --output practice`（仓库中脚本位于demo/）。选择practice/generated.mp4，SHA一致才允许播放。不同FFmpeg编码版本可能产生不同字节，脚本会报告与随包报告是否匹配；不匹配时必须重新执行下述完整生成流程并载入新报告，不可修改报告SHA蒙混关联。

切换表示、校准/后段回放和四种分段；点击分段、匿名发生位置或响应曲线定位PTS。查看全部打乱种子、背景混入和ROI偏移、预测基线及影子闸门拒绝原因。固定窗口本来就不随特征变化，稳定性不代表学会。无匹配的一致性为null。导出是原报告，不在浏览器重新拟合。

## 在仓库执行完整流程

使用Python虚拟环境和已验证的官方冻结R3D模型。以下路径是新建的私有输出，重复运行须换目录，不覆盖旧结果。生成轻量特征仍复用B.1生产器的冻结模型配置/身份核验；不宣称该生产器无需模型文件。

```bash
python -m scripts.prepare_b2u --synthetic-pair --output artifacts/b2u-review/pair --model artifacts/p036/models/r3d18-kinetics400-v1.onnx
python -m scripts.extract_b1 --config artifacts/b2u-review/pair/runtime-0/config/system.yaml --output artifacts/b2u-review/pair/features-0
python -m scripts.extract_b1 --config artifacts/b2u-review/pair/runtime-1/config/system.yaml --output artifacts/b2u-review/pair/features-1
OPENBLAS_NUM_THREADS=2 python -m scripts.run_b2u --config artifacts/b2u-review/pair/runtime-0/config/system.yaml --output artifacts/b2u-review/study
OPENBLAS_NUM_THREADS=2 python -m scripts.run_b2u --config artifacts/b2u-review/pair/runtime-1/config/system.yaml --parent artifacts/b2u-review/study --shadow-update --output artifacts/b2u-review/query
python -m scripts.verify_b2u --parent artifacts/b2u-review/study --updated artifacts/b2u-review/query --query-config artifacts/b2u-review/pair/runtime-1/config/system.yaml --output artifacts/b2u-review/verification
```

在界面载入study/report-private.json和query/query-private.json，再选择pair/generated-0.mp4。检查父分支数值完全不变，旧库条目不变，更新只产生新版本；未过闸门没有新增入库条目。同来源、修改冻结参数或来源未确认应拒绝，不能用文件SHA不同代替独立来源。

真实缓存复验（仅授权环境）：

```bash
python -m scripts.prepare_b2u --features artifacts/p037-b1/real-features/manifest.json --output artifacts/b2u-review/real-runtime
OPENBLAS_NUM_THREADS=2 python -m scripts.run_b2u --config artifacts/b2u-review/real-runtime/config/system.yaml --output artifacts/b2u-review/real-study
```

载入real-study/report-private.json并选择对应授权原片，不上传。真实跨来源需要system.yaml的source_evidence记录根来源、会话、机位、带时区采集时间和近重复审核；当前9段素材该记录未确认，因此现场独立迁移blocked。人工语义标注不是这个阻塞的原因。此前原始损坏数据库未取得与本轮离线NPZ/JSON研究无关，不重复索取。

## 自动化检查

```bash
python -m pytest tests/test_b2u_algorithms.py tests/test_local_motion_inputs.py tests/test_local_motion_analysis.py -q
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run build:demo
python tests/browser_b2u.py --output artifacts/b2u-review/browser --report artifacts/b2u-review/study/report-private.json --query artifacts/b2u-review/query/query-private.json --video artifacts/b2u-review/pair/generated-0.mp4
python -m scripts.package_b2u_demo --verify-only
python -m scripts.check_public_research_artifacts
```

Chromium实际路径可用--chromium指定。测试截图只保存生成素材；工业回查可追加--industrial-report和--industrial-video，截图在工业载入前完成。完整旧工程/科研协议检查由全量后端、前端、参考实现、旧浏览器与CI覆盖。

所有参数来自各自runtime/config/system.yaml的unlabeled字段；代码中无临时调阈值。禁止把replay用于闸门或调参。后续按当前方法取得的新数据也需在采集前冻结评价计划。代理通过只允许实验影子候选，不能解释为动作正确率、生产准入或正式科研通过。
