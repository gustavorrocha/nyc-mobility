"""Landing zone: preserva o arquivo original, versionado pelo SHA-256, sem sobrescrever."""

import hashlib
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from nyc_mobility import __version__

CHUNK_BYTES = 1 << 20


@dataclass(frozen=True)
class LandedFile:
    path: Path
    sha256: str
    size_bytes: int
    acquisition_params: dict


def download(url: str, dest_dir: Path) -> LandedFile:
    """Baixa `url` para `dest_dir/<sha256[:16]>/<nome>`.

    O conteúdo é gravado em um `.part` e só é promovido após o download completo; uma versão
    já existente com o mesmo hash é reaproveitada, e versões diferentes coexistem.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".part")
    try:
        digest, size = hashlib.sha256(), 0
        request = urllib.request.Request(url, headers={"User-Agent": f"nyc-mobility/{__version__}"})
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=60) as resp:
            expected = resp.headers.get("Content-Length")
            headers = {k: resp.headers[k] for k in ("ETag", "Last-Modified") if k in resp.headers}
            while chunk := resp.read(CHUNK_BYTES):
                digest.update(chunk)
                out.write(chunk)
                size += len(chunk)
        if expected is not None and int(expected) != size:
            raise OSError(f"Download incompleto: {size} de {expected} bytes ({url})")
        sha256 = digest.hexdigest()
        final = dest_dir / sha256[:16] / url.rsplit("/", 1)[-1]
        if final.exists():
            os.remove(tmp)
        else:
            final.parent.mkdir(exist_ok=True)
            os.replace(tmp, final)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return LandedFile(final, sha256, size, {"url": url, **headers})
