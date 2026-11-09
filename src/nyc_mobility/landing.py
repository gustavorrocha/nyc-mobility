"""Landing zone: preserva o arquivo original, versionado pelo SHA-256, sem sobrescrever."""

import hashlib
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nyc_mobility import __version__

CHUNK_BYTES = 1 << 20


@dataclass(frozen=True)
class LandedFile:
    path: Path
    sha256: str  # hash que identifica a versão (do arquivo ou do conteúdo canônico)
    size_bytes: int
    acquisition_params: dict


def download(
    url: str,
    dest_dir: Path,
    name: str | None = None,
    fingerprint: Callable[[Path], str] | None = None,
    validators: dict | None = None,
) -> LandedFile | None:
    """Baixa `url` para `dest_dir/<versão[:16]>/<nome>`.

    O conteúdo é gravado em um `.part` e só é promovido após o download completo; uma versão
    já existente é reaproveitada, e versões diferentes coexistem. `fingerprint` calcula a versão
    a partir do conteúdo canônico quando a resposta tem campos voláteis; o hash do arquivo bruto
    fica registrado em `acquisition_params.file_sha256`.

    `validators` (`ETag`/`Last-Modified` de uma versão anterior) tornam a requisição
    condicional: se a origem responder 304 (não modificado), nada é baixado e retorna None.
    """
    headers = {"User-Agent": f"nyc-mobility/{__version__}"}
    validators = validators or {}
    if "ETag" in validators:
        headers["If-None-Match"] = validators["ETag"]
    if "Last-Modified" in validators:
        headers["If-Modified-Since"] = validators["Last-Modified"]
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None
        raise
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".part")
    try:
        digest, size = hashlib.sha256(), 0
        with os.fdopen(fd, "wb") as out, resp:
            expected = resp.headers.get("Content-Length")
            headers = {k: resp.headers[k] for k in ("ETag", "Last-Modified") if k in resp.headers}
            while chunk := resp.read(CHUNK_BYTES):
                digest.update(chunk)
                out.write(chunk)
                size += len(chunk)
        if expected is not None and int(expected) != size:
            raise OSError(f"Download incompleto: {size} de {expected} bytes ({url})")
        params = {"url": url, **headers}
        version = digest.hexdigest()
        if fingerprint:
            params["file_sha256"] = version
            version = fingerprint(Path(tmp))
        final = dest_dir / version[:16] / (name or url.rsplit("/", 1)[-1])
        if final.exists():
            os.remove(tmp)
        else:
            final.parent.mkdir(exist_ok=True)
            os.replace(tmp, final)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return LandedFile(final, version, size, params)
