"""Expose the actual IRIS update routes for a supervised local browser test."""
import socket
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from core.websocket_server import app

listener = socket.socket()
listener.bind(('127.0.0.1', 0))
listener.listen(128)
print('IRIS_UPDATE_PORT=' + str(listener.getsockname()[1]), flush=True)
uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', lifespan='off', log_level='warning')).run(sockets=[listener])
