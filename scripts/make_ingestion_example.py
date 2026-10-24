"""Write an inspectable NPZ + fingerprint + request template, without GPU."""
import argparse
import json
from pathlib import Path
from packages.config.loader import load_bundle
from packages.ingestion_examples import build_example


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='config/system.yaml')
    parser.add_argument('--scene', default='anonymous_observation')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    result = build_example(load_bundle(args.config), args.scene, args.output_dir)
    fields = result['request_fields'] | {'upload_id': 'REPLACE_WITH_NPZ_UPLOAD_ID',
                                       'encoder_artifact_id': 'REPLACE_WITH_ENCODER_UPLOAD_ID'}
    (args.output_dir / 'request.json').write_text(json.dumps(fields, ensure_ascii=False, indent=2) + '\n')
    (args.output_dir / 'README.txt').write_text(
        '这是确定性合成特征，用于验证导入流程，不代表真实视频或模型效果。\n'
        'example.npz 包含 features、pts_ms、durations_ms。synthetic-encoder.json 是生成公式指纹，不是神经网络权重。\n'
        '分别 POST /api/uploads?kind=npz 和 ?kind=encoder 上传文件原始字节，再把返回 id 填入 request.json。\n'
        f'POST /api/scenes/{args.scene}/imports 提交 JSON，轮询返回任务 id。\n'
        '也可在数据接入页面点击“运行示例导入”，自动执行同一真实服务端导入流程。\n')
    print(f'Created synthetic import example in {args.output_dir}')


if __name__ == '__main__':
    main()
