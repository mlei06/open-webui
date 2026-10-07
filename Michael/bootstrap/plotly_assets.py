#!/usr/bin/env python3
"""Publish the installed Terminal renderer/font assets to the existing WebUI mount.
Run after `docker compose build open-terminal` and `up -d open-terminal`.
No model calls or user files are read. The runtime directory is private/generated.
"""
import subprocess
from pathlib import Path
from davy_connection import MICHAEL_DIR


def provision():
    prefix = ['docker', 'compose', '--env-file', str(MICHAEL_DIR / '.env'), '-f', str(MICHAEL_DIR / 'docker-compose.yaml')]
    container = subprocess.check_output(prefix + ['ps', '-q', 'open-terminal'], text=True).strip()
    if not container:
        raise RuntimeError('Start the existing open-terminal service first')
    script = "import plotly,pathlib;assert plotly.__version__=='7.0.0';print(pathlib.Path(plotly.__file__).parent/'package_data'/'plotly.min.js')"
    source = subprocess.check_output(['docker', 'exec', container, 'python', '-c', script], text=True).strip()
    assets = {source: 'plotly.min.js',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf': 'LiberationSans-Regular.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf': 'LiberationSans-Bold.ttf',
        '/usr/share/doc/fonts-liberation/copyright': 'fonts-license.txt'}
    destination = MICHAEL_DIR / 'runtime' / 'plotly'
    destination.mkdir(parents=True, exist_ok=True)
    for remote, name in assets.items():
        subprocess.run(['docker', 'cp', container + ':' + remote, str(destination / name)], check=True, capture_output=True)
        (destination / name).chmod(0o644)
    print('Published packaged Plotly 7.0.0 renderer and licensed Liberation Sans font assets.')


if __name__ == '__main__':
    provision()
