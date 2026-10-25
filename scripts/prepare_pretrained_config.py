"""Create an isolated system.yaml for the verified official video encoder."""
import argparse
import json
import os
from pathlib import Path
import shutil

import yaml
from packages.config.loader import PROJECT_ROOT, load_bundle
from packages.encoders.pretrained import PREPROCESSING_VERSION
from packages.ingestion.pipeline import file_hash


def prepare(output, model, evidence):
    output, model = output.resolve(), model.resolve()
    record = json.loads(evidence.read_text())
    if (record.get('passed') is not True or record.get('onnx_sha256') != file_hash(model)
            or record.get('preprocessing_version') != PREPROCESSING_VERSION):
        raise ValueError('Model bytes must match a passed official export record')
    config = yaml.safe_load((PROJECT_ROOT/'config/system.yaml').read_text())
    config['profiles']['demo']['encoder_weights'] = os.path.relpath(model, output)
    config['encoder'].update(adapter='onnx', feature_dim=512, pooling='global_avg_pool',
        weights_sha256=record['onnx_sha256'], preprocessing_version=PREPROCESSING_VERSION,
        checkpoint_revision='torchvision-v0.22.1-R3D_18_Weights.KINETICS400_V1')
    # Explicitly retain this source's native 640-pixel width before the official
    # resize/crop; use the model's 112-pixel input contract, keep the 128MiB budget.
    config['ingestion'].update(encoder_input_size=112, decode_width=640)
    config['video']['frames_per_clip'] = 16
    path = output/'config/system.yaml'
    if path.exists() and yaml.safe_load(path.read_text()) != config:
        raise ValueError('Refusing to overwrite a different existing configuration')
    path.parent.mkdir(parents=True, exist_ok=True)
    for source in (PROJECT_ROOT/'knowledge').rglob('*'):
        if source.is_file():
            destination = output/'knowledge'/source.relative_to(PROJECT_ROOT/'knowledge')
            if destination.exists() and destination.read_bytes() != source.read_bytes():
                raise ValueError('Refusing to overwrite changed knowledge')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    load_bundle(path)
    print(path)
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=PROJECT_ROOT/'artifacts/p031-real')
    parser.add_argument('--model', type=Path, default=PROJECT_ROOT/'artifacts/models/r3d18-kinetics400-v1.onnx')
    parser.add_argument('--encoder-report', type=Path, default=PROJECT_ROOT/'docs/delivery/p0.3.1/encoder/export.json')
    args = parser.parse_args()
    prepare(args.output_dir, args.model, args.encoder_report)
