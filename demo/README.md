# 见微 P0.1.1 离线演示

先在仓库执行：

```bash
npm --prefix apps/web run build:demo
python -m scripts.package_demo
```

生成 `demo/jianwei-p0.1.1.html` 和 `demo/jianwei-p0.1.1-demo.zip`。解压ZIP后打开index.html，或在自己的机器用纯静态服务：

```bash
python -m http.server 8001 --bind 127.0.0.1 --directory apps/web/dist-demo
```

浏览器访问本机 http://127.0.0.1:8001/ 。四场景可切换、编辑、校验和保存候选。旧P0.1本机候选只读保留，3.1候选按租户/场景隔离，不激活基线。正式候选持久化请使用FastAPI完整应用。

这只是知识管理演示，没有视频、模型训练、识别率或事件检测执行。当前执行环境未能完成浏览器截图验收，构建通过不等于完整浏览器验收通过；详见交付记录。

旧 `jianwei-p0.1.*` 与原有截图是历史版本，不能当成本次页面截图。
