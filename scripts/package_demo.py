"""Package the built standalone demo without server code or runtime state."""
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'apps/web/dist-demo/index.html'
output = ROOT / 'demo'
output.mkdir(exist_ok=True)
shutil.copyfile(source, output / 'jianwei-p0.3.2.html')
readme = '''见微 P0.3.2 多来源研究准备与预检\n\n解压后用 Chrome、Edge 或 Firefox 打开 index.html。\n这是浏览器演示，候选版本仅存在本机浏览器。\n可切换四场景，编辑动作、状态、事件和可选 SOP、校验、预览变更、保存候选并刷新查看记录。\n时序研究页新增“查看预检示例”，展示服务端实际生成的四场景元数据预检。另附历史合成训练及独立测试记录；浏览器不执行训练，不读取封存特征。\n真实文件上传、来源预检、采集记录草稿导出和研究任务需启动完整 API。未验证现场动作识别或学习效果。\n\n若企业浏览器限制本地 HTML，可在本目录运行：\npython -m http.server 8080 --bind 127.0.0.1\n再在本机浏览器访问 http://127.0.0.1:8080。\n该地址只供自己的机器使用，并非公网链接。\n\n原视频播放演示继续使用独立的 jianwei-p0.3.1-video-demo.zip。\n完整后端与代码见 https://github.com/qoxgain/jianwei/tree/codex/p0-1-foundation\n'''
with zipfile.ZipFile(output / 'jianwei-p0.3.2-demo.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for name, data in [('index.html', source.read_bytes()), ('使用说明.txt', readme.encode('utf-8'))]:
        item = zipfile.ZipInfo(name, (2026, 10, 26, 0, 0, 0))
        item.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(item, data)
print('Packaged demo/jianwei-p0.3.2.html and demo/jianwei-p0.3.2-demo.zip')
