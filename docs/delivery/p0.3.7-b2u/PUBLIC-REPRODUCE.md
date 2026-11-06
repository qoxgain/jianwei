# 不取得工业原片/私有特征的纯合成复现

适用于从GitHub新克隆的环境。只复现工程机制，不替代真实服装缓存研究或独立现场效果。Python 3.12、Node 24、FFmpeg/FFprobe已安装；在仓库中激活虚拟环境。

```bash
python -m pip install -r requirements-pretrained.txt --extra-index-url https://download.pytorch.org/whl/cpu
python demo/generate_b2u_practice.py --output artifacts/b2u-fresh/export-probe
python scripts/export_pretrained_encoder.py --download --video artifacts/b2u-fresh/export-probe/generated.mp4 --output artifacts/b2u-fresh/r3d18.onnx --report artifacts/b2u-fresh/encoder-export.json
python -m scripts.prepare_b2u --synthetic-pair --output artifacts/b2u-fresh/pair --model artifacts/b2u-fresh/r3d18.onnx
python -m scripts.extract_b1 --config artifacts/b2u-fresh/pair/runtime-0/config/system.yaml --output artifacts/b2u-fresh/pair/features-0
python -m scripts.extract_b1 --config artifacts/b2u-fresh/pair/runtime-1/config/system.yaml --output artifacts/b2u-fresh/pair/features-1
OPENBLAS_NUM_THREADS=2 python -m scripts.run_b2u --config artifacts/b2u-fresh/pair/runtime-0/config/system.yaml --output artifacts/b2u-fresh/study
OPENBLAS_NUM_THREADS=2 python -m scripts.run_b2u --config artifacts/b2u-fresh/pair/runtime-1/config/system.yaml --parent artifacts/b2u-fresh/study --shadow-update --output artifacts/b2u-fresh/query
python -m scripts.verify_b2u --parent artifacts/b2u-fresh/study --updated artifacts/b2u-fresh/query --query-config artifacts/b2u-fresh/pair/runtime-1/config/system.yaml --output artifacts/b2u-fresh/verification
```

官方checkpoint只从既有SHA固定地址下载到本地。上面用合成视频完成导出转换诊断，不依赖工业视频；实际已验证得到注册的364088…版本，框架/ONNX最大误差1.6689300537109375e-6、预处理最大误差4.947185516357422e-6、重载误差0，均满足原固定1e-4转换容差。若另一环境产生尚未注册的哈希，须保留blocked和导出证据，不自行修改固定身份。

打开R1 Demo，载入study/report-private.json及query/query-private.json，选择pair/generated-0.mp4。若新FFmpeg输出不同SHA，以本轮新报告绑定原片，不篡改已有报告。两源由不同种子分别生成，配方与源码身份分别记录；相同设计模式不是现场独立泛化证据。

这条纯合成流程只提B.1已有帧差与块运动，R3D条目明确未执行；官方模型仍用于已有生产器的冻结身份与接口校验，不虚报R3D向量。载入新报告后界面显示它实际执行的结果，不把随包历史汇总重写。所有NPZ、模型和生成视频留在artifacts，不要提交GitHub。

不想运行完整研究时，Demo包内的generate_b2u_practice.py仅需numpy和FFmpeg，可生成本机播放练习视频，不需权重；其SHA须与对应报告一致才播放。
