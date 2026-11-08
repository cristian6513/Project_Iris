"""Locate the shell owned by this verified installation, never a system Git/WSL."""
from pathlib import Path
import stat

from core.runtime_descriptor import RuntimeDescriptorError


def private_shell_bin(runtime) -> Path:
    base = runtime.base
    try:
        if not runtime.packaged or not base.is_absolute() or base.resolve(strict=True) != base:
            raise ValueError
        current = base
        for part in ("tools", "shell", "usr", "bin"):
            current /= part
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or current.resolve(strict=True) != current:
                raise ValueError
        for path in (current / "bash.exe", current / "sh.exe", current / "msys-2.0.dll", base / "tools/shell/etc/fstab"):
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_size == 0 or info.st_nlink != 1
                    or getattr(info, "st_file_attributes", 0) & 0x400 or path.resolve(strict=True) != path):
                raise ValueError
        return current
    except (OSError, ValueError, RuntimeError) as exc:
        raise RuntimeDescriptorError("La consola privada de Iris falta o contiene un alias. Repara Iris con su instalador.") from exc
