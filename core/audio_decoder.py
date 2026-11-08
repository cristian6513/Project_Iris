"""Bounded audio decoding with the Windows package's private FFmpeg.

The package layout is fixed, independently of cwd, PATH and codec environment
variables. Build-time inventories attest the binary hashes and licenses; this
resolver additionally rejects filesystem aliases before each process launch.
"""

from __future__ import annotations

import os
import stat
import subprocess
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

import numpy as np

from core.runtime_descriptor import RuntimeContext, RuntimeDescriptorError, get_runtime

MAX_AUDIO_INPUT_BYTES = 32 * 1024 * 1024
MAX_AUDIO_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_AUDIO_ERROR_BYTES = 64 * 1024
AUDIO_TIMEOUT_SECONDS = 30
AUDIO_SAMPLE_RATE = 24_000
_TOOLS = frozenset({"ffmpeg", "ffprobe"})


def _ordinary(path: Path, *, directory: bool) -> None:
    try:
        info = path.lstat()
        valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (
            not valid_type
            or getattr(info, "st_file_attributes", 0) & 0x400
            or (not directory and (info.st_nlink != 1 or info.st_size == 0))
        ):
            raise ValueError("Not an ordinary package resource")
    except (OSError, ValueError) as exc:
        raise RuntimeDescriptorError(
            "El decoder de audio privado falta o contiene un alias."
        ) from exc


def private_audio_bin(runtime: RuntimeContext | None = None) -> Path:
    """Resolve only the attested package layout, never an external installation."""
    runtime = get_runtime() if runtime is None else runtime
    if not runtime.packaged:
        raise RuntimeDescriptorError("El decoder privado requiere el runtime empaquetado.")
    base: Path = runtime.base
    _ordinary(base, directory=True)
    try:
        if not base.is_absolute() or base.resolve(strict=True) != base:
            raise ValueError("Invalid installation base")
        current = base
        for part in ("tools", "audio", "bin"):
            current /= part
            _ordinary(current, directory=True)
            if current.resolve(strict=True) != current:
                raise ValueError("Aliased audio directory")
        files = list(current.iterdir())
        names = {item.name for item in files}
        if not {"ffmpeg.exe", "ffprobe.exe"} <= names or not any(
            item.suffix.lower() == ".dll" for item in files
        ):
            raise ValueError("Incomplete shared decoder")
        for item in files:
            if item.name not in {"ffmpeg.exe", "ffprobe.exe"} and item.suffix.lower() != ".dll":
                raise ValueError("Unknown audio binary")
            _ordinary(item, directory=False)
            if item.resolve(strict=True) != item:
                raise ValueError("Aliased audio binary")
        return current
    except (OSError, ValueError, RuntimeError) as exc:
        raise RuntimeDescriptorError(
            "El decoder de audio privado no está completo o es ambiguo."
        ) from exc


def private_audio_tool(name: str, runtime: RuntimeContext | None = None) -> Path:
    """Only two known tool names are valid; names never become arbitrary paths."""
    if not isinstance(name, str) or name not in _TOOLS:
        raise RuntimeDescriptorError("Herramienta de audio desconocida.")
    return private_audio_bin(runtime) / f"{name}.exe"


def _decoder_env(bin_path: Path) -> dict[str, str]:
    # FFREPORT can create files and PATH can select other codecs. FFmpeg needs
    # neither the user's profile nor provider credentials for a pipe decode.
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env["PATH"] = str(bin_path)
    return env


def _bounded_run(
    command: Sequence[str],
    audio_bytes: bytes,
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float = AUDIO_TIMEOUT_SECONDS,
    output_limit: int = MAX_AUDIO_OUTPUT_BYTES,
    error_limit: int = MAX_AUDIO_ERROR_BYTES,
) -> bytes:
    """Drain pipes concurrently and kill before retaining unbounded output."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=cwd, env=dict(env), shell=False,
            creationflags=flags,
        )
    except OSError as exc:
        raise RuntimeError("No se pudo iniciar el decoder de audio privado.") from exc

    stdin, stdout, stderr = process.stdin, process.stdout, process.stderr
    assert stdin is not None and stdout is not None and stderr is not None

    output = bytearray()
    diagnostic = bytearray()
    failures: list[str] = []

    def kill() -> None:
        try:
            process.kill()
        except OSError:
            pass

    def drain(stream: BinaryIO, target: bytearray, limit: int) -> None:
        try:
            while block := stream.read(64 * 1024):
                if len(target) + len(block) > limit:
                    failures.append("El audio supera el límite del decoder privado.")
                    kill()
                    return
                target.extend(block)
        except OSError:
            failures.append("El decoder privado no devolvió una salida completa.")
            kill()
        finally:
            stream.close()

    def feed() -> None:
        try:
            stdin.write(audio_bytes)
            stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # FFmpeg's exit code and output determine validity.
        finally:
            try:
                stdin.close()
            except OSError:
                pass

    workers = [
        threading.Thread(target=drain, args=(stdout, output, output_limit), daemon=True),
        threading.Thread(target=drain, args=(stderr, diagnostic, error_limit), daemon=True),
        threading.Thread(target=feed, daemon=True),
    ]
    for worker in workers:
        worker.start()
    timeout_error: subprocess.TimeoutExpired | None = None
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        kill()
        process.wait(timeout=5)
        failures.append("El decoder de audio privado agotó su tiempo de espera.")
        timeout_error = exc
    finally:
        for worker in workers:
            worker.join(timeout=5)
    if any(worker.is_alive() for worker in workers):
        kill()
        raise RuntimeError("El decoder de audio privado no cerró sus canales.")
    if failures:
        raise RuntimeError(failures[0]) from timeout_error
    if process.returncode != 0:
        raise RuntimeError("El decoder privado rechazó el formato o el contenido del audio.")
    return bytes(output)


def decode_private_audio(
    audio_bytes: bytes, runtime: RuntimeContext | None = None,
) -> tuple[np.ndarray, int]:
    """Decode one bounded audio stream to mono int16 PCM at 24 kHz."""
    if not isinstance(audio_bytes, bytes) or not audio_bytes:
        raise RuntimeError("El audio está vacío o no es una secuencia de bytes.")
    if len(audio_bytes) > MAX_AUDIO_INPUT_BYTES:
        raise RuntimeError("El audio supera el límite de entrada del decoder privado.")
    executable = private_audio_tool("ffmpeg", runtime)
    command = [
        str(executable), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-threads", "1", "-protocol_whitelist", "pipe", "-i", "pipe:0",
        "-map", "0:a:0", "-vn", "-sn", "-dn", "-f", "s16le",
        "-acodec", "pcm_s16le", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "1", "pipe:1",
    ]
    raw = _bounded_run(
        command, audio_bytes, cwd=executable.parent, env=_decoder_env(executable.parent),
    )
    if not raw or len(raw) % 2:
        raise RuntimeError("El decoder privado devolvió PCM vacío o incompleto.")
    return np.frombuffer(raw, dtype="<i2"), AUDIO_SAMPLE_RATE
