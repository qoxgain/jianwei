"""Generate a candid quantitative report from completed actual experiments."""
import json
from pathlib import Path
import numpy as np
from packages.config.loader import PROJECT_ROOT


def main():
    base=PROJECT_ROOT/'artifacts/exploratory';r=json.loads((base/'final/results.json').read_text())
    if r['pending'] or len(r['assets'])!=9:raise ValueError('Batch not complete')
    rows=[];horizon=[];positive=[];degraded=[];executed=0;features=0
    for a in sorted(r['assets'],key=lambda x:x['asset_id']):
        features+=a['rows'];executed+=sum(m['status']=='executed' for m in a['modes'].values());b=a['modes']['B']
        if b['status']!='executed':rows.append(f"| {a['asset_id']} | {a['rows']} | {a['modes']['A']['counts']['train']} | blocked | — | — | — | — | — |");continue
        m=b['metrics']['holdout'];v={k:float(np.mean(x['feature_mse'])) for k,x in m.items()}
        pos=all(v['causal_tcn']<v[k] for k in ('persistence','ridge','train_mean','initial_tcn','shuffled_tcn'))
        if pos:positive.append(a['asset_id'])
        if v['causal_tcn']>=v['persistence']:degraded.append(a['asset_id'])
        imp=100*(v['persistence']-v['causal_tcn'])/v['persistence']
        rows.append(f"| {a['asset_id']} | {a['rows']} | {a['modes']['A']['counts']['train']} | {'/'.join(str(b['counts'][k]) for k in ('train','validation','holdout'))} | {v['persistence']:.6f} | {v['ridge']:.6f} | {v['causal_tcn']:.6f} | {v['train_mean']:.6f} | {imp:+.2f}% |")
        for h,seconds in enumerate(a['learning']['forecast_horizons_seconds']):
            horizon.append({'asset':a['asset_id'],'horizon_seconds':seconds,'models':{k:{metric:x[metric][h] for metric in ('feature_mse','normalized_feature_mse','feature_cosine')} for k,x in m.items()}})
    out=PROJECT_ROOT/'docs/delivery/exploratory-real-video'
    (out/'per-horizon.json').write_text(json.dumps(horizon,ensure_ascii=False,indent=2)+'\n')
    report=f'''# jianwei真实工业视频探索性自学习实验报告

日期：2026-11-02。审计基线0bf8fbab9b986940eb8d24a92a7435db06f03ea3。独立分支`codex/p0-3-5-real-video-exploration-20261102`，原分支与正式数据/消费记录未修改。此报告为回顾性探索，不能作为正式封存测试。

## 四类结论分别列示

| 结论类别 | 本次证据 |
|---|---|
| 工程机制验证 | 新增隔离流程和反例/边界回归；批量API出现一次隔离SQLite损坏，不能据此宣布全链路生产就绪。 |
| 真实训练实际执行 | 九段原视频共{features}个真实512维有效片段；{executed}组A/B实验实际运行，包含{executed*2}次TCN训练（含打乱对照）、真实Ridge与权重逐数组重载。基础编码器保持冻结。 |
| 探索性正向结果 | B中有{len(positive)}个来源的TCN按三跨度均值同时低于Persistence、Ridge、训练均值、初始TCN和ShuffledTCN；这只是回顾性数值比较，不是统计显著性或独立泛化通过。 |
| 正式独立科研效果 | blocked；已核验独立采集会话为0；不改变正式门槛，不消费正式封存资料，不自动激活或准入P0.4。 |

## 真实素材与版本

九段MP4的原始SHA/大小与catalog一致；格式、旋转、帧率和时长见[inventory.json](exploratory-real-video/inventory.json)。七个公牛工位分别建模，不当作同一视角的重复会话。采集时间、物理机位、独立会话仍未知，不以文件creation_time代填。

服装test与testShort的2fps、32×24灰度标准化时序检索没有确认直接重合，最优完整短片对齐MSE约1.21077，精确样本匹配0；但裁剪、转码、变速和未采样区间可能逃过筛查，因此**关系仍未核实**，不据此承认独立采集。见[筛查原始结果](exploratory-real-video/garment-overlap.json)。本流程没有发布帧图。

历史P0.3.3的695个稀疏片段使用2秒步长；0.5秒预测落到下一格产生1.5秒偏差，超过1秒容差，联合窗口为0。旧编码器/预处理哈希也不能与新版本混用。本次全部重新使用已核验正式冻结R3D-18 CPU ONNX、2秒片段/0.5秒步长/16帧提取，并保留每个源的独立特征空间、原始清单、实际缓存SHA和生产记录。模型SHA来自已有通过的正式导出记录并经本机文件核对；本机未重跑需PyTorch的三项预处理专项，不能将跳过说成通过。

这次是视频单模态特征实验，没有验证音频/传感器融合，也没有引入SOP或动作类别作为自监督标签。

## 固定实验设计

A：同视频同窗口训练与评价，只检验拟合。B：先按原PTS时长50%/75%切分，train=[0,50%)，validation=[50%+2s,75%)，holdout=[75%+2s,end)。先排除所有跨界片段，再在各自episode/时间域内构建历史与目标，检查三个分区无共享片段；实际片段的选帧限定于其半开原始PTS区间，因此2秒保护间隔下不共享输入帧和未来目标。时间域不唯一/起点不明的B拒绝自动拼接。

历史8，预测0.5/2/5秒，容差仍1000ms；每分区至少8窗口，不足保持blocked。20.44秒短片只能做A，没有缩短跨度或保护间隔。PCA16、标准化、匿名聚类只用train；验证区间选择Persistence/训练均值/Ridge/TCN后，先保存选择记录再计算holdout。不同视频的特征、训练参数和归一化没有合并。

使用根学习配置：hidden32、20epochs、Adam lr0.001、batch64、Ridge L2=1、seed23。TCN、Ridge与PCA复用原数值实现。另训练ShuffledRidge和同架构ShuffledTCN，保留InitialTCN与TrainMean，避免把回归均值/预处理收益误当作时序学习。打乱仅改变train目标顺序，保持目标边际分布；重叠邻近目标仍可能相似，不能把该负对照说成消除了所有相关性。没有根据不利结果重新选参数。

## B：真实留出区间的量化结果

以下MSE为**原512维特征空间**三个跨度的算术均值，视频各自列示，不将跨工位平均当作总体泛化。完整七模型、三个跨度、原空间余弦与归一化误差见[每跨度记录](exploratory-real-video/per-horizon.json)。窗口有相关性，不构造窗口级置信区间。

| 视频 | 有效片段 | A窗口 | B训练/验证/留出 | Persistence | Ridge | TCN | TrainMean | TCN对Persistence |
|---|---:|---:|---|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

TCN在以下来源按均值同时优于五项重要对照：{', '.join(positive) or '无'}。相对Persistence持平/退化的来源：{', '.join(degraded) or '无'}。这不是通过率；各跨度可能出现相反方向，需要逐项看原始记录。

单视频A的Ridge通常可获得较低拟合误差，但A数据复用，没有泛化证据。B的Ridge在部分来源明显退化，表明A的拟合优势不能外推到稍后时间区间。TCN较Persistence的较大改善也可能主要来自回归均值：例如服装长片的TrainMean与TCN非常接近，不能把全部改善归因于时间关系。相对InitialTCN与ShuffledTCN的小差异尤其需要更多独立会话验证。

**对“是否真正学会”的判断：**真实参数更新、真实特征拟合与权重重载已证实；部分未参与训练时间区间存在可预测性和小幅时序优势，但当前不能确认稳定跨来源的时序学习收益。没有动作真值，不报告动作识别、事件或SOP准确率；匿名簇只是训练数据拟合的回放代理，不输出语义NLL/Brier或显著性结论。

## C、失败与工程边界

C未执行。七工位任务/视角不同，服装两片的谱系/采集身份未核实，不能做同分布留一来源验证。也没有将跨域结果包装成独立科研效果。

批量API的第二个隔离库在工位4期间出现`database disk image is malformed`；随后旧轮询驱动未验证HTTP响应，触发`KeyError: status`。数据库损坏根因未建立，不能归因于特定源码或证明原正式库受损。失败日志、库SHA与状态保留在[失败记录](exploratory-real-video/failures/runtime-incident.json)。驱动在本实验分支增加HTTP/结构检查和4项反例测试。未完成源改用**同一个process_import冻结管线**直接文件输出，不写数据库，不改变采样、权重或评价条件；各源执行方式见提取记录。重复提取任务取消和失败不计为学习成功。

资源受CPU解码和冻结编码限制，运行时长仅属于本机测量，不构成性能承诺。新的协议/真实会话能力未合并到原分支；审核发现的正式窗口配额和特征实现版本绑定缺口仍须单独审阅修复。

## Demo、权重与复现

前端新增“真实视频学习实验”，支持九来源、A/B切换、每跨度/分区对比、实际训练损失、未来观测误差、向量范数、train-only匿名簇回放、报告导出和本机原片SHA核验回查。曲线只取自实际记录，展示端最多均匀抽取160个真实曲线点/300个簇点，不伪造平滑曲线。原片/帧/PDF不打入Demo，也不新增公开画面。HEVC不能在浏览器播放时明确提示使用本机兼容播放器。

数值交付包含实际512维缓存、全部执行模型权重、配置、来源/划分、日志与逐源报告；完整512维预测数组可用相同特征和权重重建。ZIP条目/大小/SHA见[bundle.json](exploratory-real-video/bundle.json)。默认不包含原片、PDF、图片或原始数据库。[代码与复现说明](../../experiments/real_video/README.md)；正式冻结模型安装仍按原README。重新训练时必须使用记录中的同一采样、模型/预处理和依赖版本，不覆盖历史输出。

## P0.4与正式验收所需数据

已具备继续完善P0.3和设计P0.4语义接口的工程基础，但**不具备以P0.3科研效果通过为前提的正式P0.4准入证据**。不启动自动SOP判罚、在线持续训练或模型自动激活。

每个固定视角需新增真实独立采集会话，补相机/视角、带时区采集时间、会话ID、根/派生谱系和近重复审核；至少2 train、1 calibration、协议规定的至少2 test只是软件下限，科学结论应增加独立日期、班次、人员与足量重复操作。新test须事先封存，现有九片不能再冒充新封存测试。训练前登记协议、指标、对照和改善门槛，按会话等权评估。若要验收语义动作/步骤，另采人工标注及复核资料；工艺质量要求另提供仪器证据。
'''
    (PROJECT_ROOT/'docs/delivery/REAL-VIDEO-EXPLORATORY.md').write_text(report)
    print('Report sources:',len(r['assets']),'executed A/B:',executed,'feature rows:',features)


if __name__=='__main__':main()
