from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
app = FastAPI()
@app.get('/api/health')
def health():
    return {'status': 'ok', 'app': 'workspace-starter'}
app.mount('/', StaticFiles(directory=Path(__file__).parent / 'static', html=True), name='static')
