# 见微 P0.2 演示

下载 `jianwei-p0.2-demo.zip`，解压后打开 `index.html`。若企业浏览器限制本地 HTML，在解压目录运行 `python -m http.server 8001 --bind 127.0.0.1`，再用本机浏览器访问该地址。这不是已部署的公网服务。

可切换四场景、编辑知识并保存本机候选；在“数据接入”点击“查看示例流程”，查看真实服务端合成输入产生的静态清单、周期、特征空间和来源记录。独立 HTML 不上传文件或运行服务端队列，不代表真实视频识别或训练。

完整文件导入请按仓库 README 启动 API，然后进入“数据接入”运行示例或上传 MP4/NPZ。实际浏览器截图及测试记录见 [P0.2 交付](../docs/delivery/P0.2.md)。

重建：

```bash
npm --prefix apps/web run build:demo
.venv/bin/python -m scripts.package_demo
```

旧 `jianwei-p0.1.*`、`jianwei-p0.1.1.*` 为历史交付，不替代当前验收证据。
