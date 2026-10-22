"""Export only the actual implemented HTTP surface; never advertise future routes."""
from pathlib import Path
import yaml
from apps.api.main import create_app

if __name__ == '__main__':
    path=Path(__file__).resolve().parents[1]/'contracts/openapi.yaml'
    path.write_text(yaml.safe_dump(create_app().openapi(),allow_unicode=True,sort_keys=False))
    print('Generated implemented OpenAPI snapshot')
