# 现场视频与 SOP 测试素材

本目录保存用户上传的原始素材，按来源组织；不转码、不修改原始字节。用途为开发、回归和探索，**不是已通过来源审查的训练/校准/封存测试集**。上传基线：`54e1068600e96c51db575f531d50fba9464181cb`。

| 路径 | 内容 |
|---|---|
| `bull_new_energy/videos/岗1.mp4` 至 `岗7.mp4` | 公牛新能源生产操作视频，保留工位文件名 |
| `bull_new_energy/sop/SOP文档.pdf` | 原始规范，26个PDF页面 |
| `bull_new_energy/sop/outline.draft.json` | 11个编号章节、页码与文档疑点，待审核 |
| `bull_new_energy/sop/station-mapping.draft.json` | 六帧抽样形成的候选映射，不是动作真值 |
| `garment_quality/videos/test.mp4`、`testShort.mp4` | 两个服装视频，来源关系待核实 |
| `catalog.json` | 文件哈希、大小、技术信息与待补来源字段 |
| `relocation.json` | 旧新路径、SHA256与原Git blob迁移台账 |

9个视频共1,582,901,359字节，约23分18秒；PDF为3,177,448字节。视频使用Git LFS，Git中的小文件是指针。移动复用原LFS OID和PDF blob，不重复上传1.58 GB视频。

只取历史回归视频（CI使用此方式）：

```bash
git lfs install
git lfs pull --include="datasets/video_samples/garment_quality/videos/test.mp4" --exclude=""
python scripts/audit_video_samples.py --asset garment_test --output artifacts/sample-integrity.json
```

取全部现场素材并逐帧解码检查：

```bash
git lfs pull --include="datasets/video_samples/**/*.mp4" --exclude=""
python scripts/audit_video_samples.py --full-decode --output artifacts/video-sample-audit.json
```

脚本逐块检查大小与SHA256；`--probe`读取技术信息，`--full-decode`解码完整视频流并核对帧数，不训练或生成转码文件。退出码0为检查通过，1为失败，2为缺文件、未下载LFS实体或工具/时间限制阻塞。不检查音轨质量或动作/SOP准确率。

`catalog.json`不是`temporal.dataset_manifest`；asset_id不是已核验的camera/view/session。容器creation_time仅作线索，不能替代真实采集记录。7个工位可能属于一次访问，不能按5/1/1文件数随意划分；不能因testShort的名字自动判为test的截片。来源与分区保持unknown/unassigned/pending。

当前素材已用于开发审阅。最终封存测试应另采未参与调参的独立录像。公牛SOP仅用于确认适用的产品，不能应用于服装数据。规范、观测、预测、人工真值分开；草稿不自动激活。

默认64 MiB上限不能接收7个工位视频，现有缩放还存在旋转几何问题。素材解码成功不等于产品端到端适配通过。参见 [实际评估](../../docs/delivery/P0.3.3-data-review.md)、[研发任务书](../../docs/plans/P0.3.3-real-data.md)、[Codex指令](../../docs/plans/P0.3.3-Codex.txt)。

`examples/`保留协议样例。历史P0.3.1报告、HTML和ZIP保持原哈希；浏览器验收服务器对旧`/examples/test.mp4`提供精确只读别名。普通静态服务器没有该别名时，选择新路径本地文件，或使用内嵌视频的历史ZIP。旧GitHub分支路径不再是视频入口，请更新书签；固定历史commit的记录保留。
