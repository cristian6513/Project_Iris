"""One validated, versioned view of the committed animation library."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from core.animation_store import (
    ANIMATION_EXTENSIONS,
    MANAGED_FILENAME,
    MAX_UPLOAD_BYTES,
    AnimationStore,
    AnimationStoreError,
    _plain_file,
)


def animation_snapshot(root: Path, user_root: Path | None = None) -> dict:
    if user_root is not None:
        from core.animation_library import AnimationLibrary

        return AnimationLibrary(root, user_root).snapshot()
    store = AnimationStore(root)
    manifest = store._manifest()
    resources = {}
    for name, filename in manifest["clips"].items():
        path = _plain_file(store.root, filename)
        extension = filename.rsplit(".", 1)[-1].lower()
        if extension not in ANIMATION_EXTENSIONS:
            raise AnimationStoreError("Formato registrado no compatible.", 409)
        if MANAGED_FILENAME.fullmatch(filename):
            digest = filename.rsplit(".", 2)[1]
        else:
            # Factory/legacy filenames predate A02. Fingerprint their bytes too;
            # a fixed filename must not conceal a changed revision.
            with path.open("rb") as handle:
                content = handle.read(MAX_UPLOAD_BYTES + 1)
            if len(content) > MAX_UPLOAD_BYTES:
                raise AnimationStoreError("Animación registrada demasiado grande.", 409)
            digest = hashlib.sha256(content).hexdigest()
        resources[name] = {
            "filename": filename,
            "format": extension,
            "revision": digest,
            "url": f"/face/animations/{quote(filename)}?v={digest}",
        }
    snapshot = {"manifest": manifest, "resources": resources}
    raw = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    return {**snapshot, "revision": hashlib.sha256(raw).hexdigest()}
