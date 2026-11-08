"""Standalone, stdlib-only runner for an explicitly installed external app.

Started lazily by Iris' private Python with -I -S -B. A separate process is NOT an OS
sandbox. The app has the user's rights; stdout is reserved for correlated JSON replies.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import os
from pathlib import Path
import queue
import sys
import sysconfig
import threading
from types import SimpleNamespace


def main():
    protocol = sys.stdout
    sys.stdout = sys.stderr
    pending = queue.Queue(maxsize=2)

    def reader():
        while True:
            line = sys.stdin.buffer.readline(65537)
            if not line:
                # Also stop a stuck handler when the owning Core's pipe closes.
                os._exit(0)
            if len(line) > 65536 or not line.endswith(b'\n'):
                os._exit(2)
            pending.put(line)

    threading.Thread(target=reader, daemon=True).start()
    folder, entry, data = map(Path, sys.argv[1:4])
    # The packaged embeddable Python has an explicit ._pth which can still name
    # site-packages under -S. Keep its stdlib/native directories, not Iris' or
    # Hermes' dependencies. This is dependency isolation, not an OS sandbox.
    base = Path(sys.base_prefix).resolve()
    stdlib = Path(sysconfig.get_path('stdlib')).resolve()
    version = f'{sys.version_info.major}{sys.version_info.minor}'
    allowed = {base, stdlib, stdlib / 'lib-dynload', base / 'DLLs',
               base / f'python{version}.zip', base / 'lib' / f'python{version}.zip'}
    sys.path[:] = [item for item in sys.path if Path(item).resolve() in allowed]
    sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location('iris_external_app', folder / entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = module.handle
    context = SimpleNamespace(data_dir=data)
    while True:
        request = json.loads(pending.get())
        if request.get('schema') != 'iris.app-worker/v1':
            return 2
        answer = {'schema': 'iris.app-worker-result/v1', 'id': request['id']}
        # Assigned by Core from the route the call came by: 'ui' (the app's window or widget) or 'iris'.
        context.caller = request.get('caller')
        try:
            result = handler(request['action'], request['args'], context)
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            if not isinstance(result, dict):
                raise TypeError('handle must return a dict')
            answer.update(status='ok', data=result)
            encoded = json.dumps(answer, ensure_ascii=True, allow_nan=False)
            if len(encoded.encode()) > 49152:
                raise ValueError('response too large')
        except Exception as error:
            # Do not send arbitrary exceptions/tracebacks (possibly containing secrets).
            answer.update(status='error', data={}, message=f'La app no completó la operación ({type(error).__name__[:60]}).')
            encoded = json.dumps(answer)
        protocol.write(encoded + '\n')
        protocol.flush()


if __name__ == '__main__':
    main()
