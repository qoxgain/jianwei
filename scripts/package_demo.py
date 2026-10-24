"""Package the built standalone demo without server code or runtime state."""
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'apps/web/dist-demo/index.html'
output = ROOT / 'demo'
output.mkdir(exist_ok=True)
shutil.copyfile(source, output / 'jianwei-p0.2.html')
readme = '''见微 P0.2 数据接入与知识工作台\n\n解压后用 Chrome、Edge 或 Firefox 打开 index.html。\n这是浏览器演示，候选版本仅存在本机浏览器。\n可切换四场景，编辑动作、状态、事件和可选 SOP、校验、预览变更、保存候选并刷新查看记录。\n数据接入页可查看四场景服务端实际生成的合成示例清单。独立HTML不运行服务端导入；真实文件上传、异步任务和重启恢复需启动完整API。当前没有真实视频识别、训练或评测指标。\n\n若企业浏览器限制本地 HTML，可在本目录运行：\npython -m http.server 8080 --bind 127.0.0.1\n再在本机浏览器访问 http://127.0.0.1:8080。\n该地址只供自己的机器使用，并非公网链接。\n\n完整后端与代码见 https://github.com/qoxgain/jianwei/tree/codex/p0-1-foundation\n'''
with zipfile.ZipFile(output / 'jianwei-p0.2-demo.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for name, data in [('index.html', source.read_bytes()), ('使用说明.txt', readme.encode('utf-8'))]:
        item = zipfile.ZipInfo(name, (2026, 10, 23, 0, 0, 0))
        item.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(item, data)
print('Packaged demo/jianwei-p0.2.html and demo/jianwei-p0.2-demo.zip')
