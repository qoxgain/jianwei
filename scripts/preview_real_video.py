"""Export an auditable, unassigned-source discovery report and offline viewer."""
import argparse
import html
import json
from pathlib import Path
from urllib.parse import quote

from packages.config.loader import PROJECT_ROOT, load_bundle
from packages.ingestion.pipeline import file_hash
from packages.temporal.exploration import explore


def render_html(report, video_url):
    template = (PROJECT_ROOT / 'demo/p031-preview/template.html').read_text(encoding='utf-8')
    # JSON in a script element must not allow </script>, HTML comments or U+2028.
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    payload = payload.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    return template.replace('__VIDEO_URL__', html.escape(video_url, quote=True)).replace('__REPORT_JSON__', payload)


def capture(manifest_path, vectors_path, video_path, config_path, output_dir, video_url=None, encoder_report=None):
    bundle = load_bundle(config_path)
    if bundle.config.temporal is None:
        raise ValueError('当前配置未启用时序研究')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    report = explore(manifest, vectors_path, video_path, bundle.config.temporal, bundle.config_hash)
    if encoder_report:
        # Keep the original verification record and digest. Its provenance is
        # reported verbatim; a generic frozen ONNX graph is never called pretrained.
        record = json.loads(encoder_report.read_text(encoding='utf-8'))
        if record.get('passed') is not True or record.get('onnx_sha256') != report['feature_space']['encoder_sha']:
            raise ValueError('编码器验收记录未通过或与本次特征的实际 ONNX SHA256 不一致')
        report['encoder_verification_record'] = record
        report['encoder_verification_record_sha256'] = file_hash(encoder_report)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    url = video_url if video_url is not None else quote(video_path.name)
    (output_dir / 'index.html').write_text(render_html(report, url), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--vectors', type=Path, required=True)
    parser.add_argument('--video', type=Path, default=PROJECT_ROOT / 'datasets/video_samples/garment_quality/videos/test.mp4')
    parser.add_argument('--config', type=Path, default=PROJECT_ROOT / 'config/system.yaml')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--video-url', help='Relative URL to the unchanged source video beside the exported HTML')
    parser.add_argument('--encoder-report', type=Path, help='Optional original frozen-model export verification record')
    args = parser.parse_args()
    result = capture(args.manifest, args.vectors, args.video, args.config, args.output_dir, args.video_url, args.encoder_report)
    print(json.dumps({'status': result['status'], 'eligible_rows': result['eligible_rows'],
                      'segments': len(result['segments']), 'coverage': result['coverage']['fraction']}, ensure_ascii=False))
