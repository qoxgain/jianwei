# 交接包参考工具（冻结基线）

来自 2026-10-15 研发交接包，仅用于复现合成特征机制验证。这里的配置与代码是原始参考快照；正式应用只使用仓库根目录的 `config/system.yaml` 与 `knowledge/labels.yaml`。

从本目录运行：

```sh
../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python tools/config_doctor.py --config config/system.yaml
../.venv/bin/python tools/self_learning_demo.py --config config/system.yaml
```

参考工具的预测、记忆和指标仅来自合成特征，不代表真实视频效果。
