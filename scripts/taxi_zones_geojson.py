# /// script
# requires-python = ">=3.12"
# dependencies = ["pyshp>=2.3", "pyproj>=3.6", "shapely>=2.1"]
# ///
"""Gera dashboard/assets/taxi_zones.geojson a partir do shapefile oficial das Taxi Zones (TLC).

Execução única, fora do pipeline (dependências declaradas só aqui):

    uv run scripts/taxi_zones_geojson.py

- Reprojeta do sistema do shapefile (NY State Plane, pés) para WGS84 (lon/lat).
- Une registros da mesma LocationID (o shapefile tem zonas em mais de um registro).
- Simplifica (~10 m) e arredonda para 5 casas (~1 m), reduzindo o arquivo servido ao navegador.
"""

import hashlib
import io
import json
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

import shapefile
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip"
OUTPUT = Path(__file__).parents[1] / "dashboard" / "assets" / "taxi_zones.geojson"
TOLERANCE_DEG = 0.0001  # ~10 m


def _member(archive: zipfile.ZipFile, suffix: str) -> io.BytesIO:
    (name,) = [n for n in archive.namelist() if n.lower().endswith(suffix)]
    return io.BytesIO(archive.read(name))


def _round(coords):
    if isinstance(coords[0], float):
        return [round(c, 5) for c in coords]
    return [_round(c) for c in coords]


def main() -> None:
    with urllib.request.urlopen(URL, timeout=60) as response:
        payload = response.read()
    archive = zipfile.ZipFile(io.BytesIO(payload))
    crs = CRS.from_wkt(_member(archive, ".prj").read().decode())
    to_wgs84 = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    reader = shapefile.Reader(
        shp=_member(archive, ".shp"), shx=_member(archive, ".shx"), dbf=_member(archive, ".dbf")
    )

    parts = defaultdict(list)
    for record in reader.iterShapeRecords():
        parts[int(record.record["LocationID"])].append(shape(record.shape.__geo_interface__))

    features = []
    for zone_id in sorted(parts):
        geometry = shapely.transform(unary_union(parts[zone_id]), to_wgs84, interleaved=False)
        geometry = mapping(geometry.simplify(TOLERANCE_DEG, preserve_topology=True))
        geometry = {"type": geometry["type"], "coordinates": _round(geometry["coordinates"])}
        features.append(
            {"type": "Feature", "properties": {"zone_id": zone_id}, "geometry": geometry}
        )

    collection = {
        "type": "FeatureCollection",
        "source": {"url": URL, "sha256": hashlib.sha256(payload).hexdigest(), "crs": crs.name},
        "features": features,
    }
    OUTPUT.write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")
    print(
        f"{len(features)} zonas ({reader.numRecords} registros no shapefile) -> {OUTPUT} "
        f"({OUTPUT.stat().st_size / 1024:.0f} KiB); sha256 do zip: {collection['source']['sha256']}"
    )


if __name__ == "__main__":
    main()
