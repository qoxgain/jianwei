# 见微演示

P0.3.1 新增 [真实视频匿名探索包](jianwei-p0.3.1-video-demo.zip)：解压后打开 `index.html`，内含原视频、117×512 实际特征、17 个匿名候选分段和报告。点击代表片段回查原画面。只有一个独立来源，没有动作准确率或独立学习效果结论。[实际截图与验收](../docs/delivery/P0.3.1.md)。

下载 `jianwei-p0.3-demo.zip`，解压后打开 `index.html`。若企业浏览器限制本地 HTML，在解压目录运行 `python -m http.server 8001 --bind 127.0.0.1`，再用本机浏览器访问该地址。这不是已部署的公网服务。

可切换四场景、编辑知识并保存本机候选；在“数据接入”点击“查看示例流程”，查看真实服务端合成输入产生的静态清单、周期、特征空间和来源记录。独立 HTML 不上传文件或运行服务端队列，新增“时序研究”展示已执行的合成训练和独立测试，不能外推为真实视频识别效果。

完整文件导入请按仓库 README 启动 API，然后进入“数据接入”运行示例或上传 MP4/NPZ。P0.3 四场景研究页面的浏览器截图已在 [P0.3.1 交付](../docs/delivery/P0.3.1.md)补齐；原自动测试和研究证据见 [P0.3 交付](../docs/delivery/P0.3.md)。

重建：

```bash
npm --prefix apps/web run build:demo
.venv/bin/python -m scripts.package_demo
```

旧 `jianwei-p0.1.*`、`jianwei-p0.1.1.*`、`jianwei-p0.2.*` 为历史交付，不替代当前验收证据。
