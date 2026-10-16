"""Extract level assets from the early Soul Reaver PSX BIGFILE.

The July 1999 prototype uses a BIGFILE index variant that is not accepted by
the retail/demo archive reader.  Its CATDATA catalog still records the path
and 0x800-aligned byte offset for every stored file, so extraction can be
performed without decoding the alternate index.  Area ``.drm`` files are
relocatable level scenes and can be staged with a ``.pcm`` extension for
remaster/viewer experiments; this command deliberately does not rewrite
their internal pointers or mutate the source archive.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


RECORD_RE = re.compile(r"^(?P<path>\\[^:]+)\s*:\s+(?P<fields>.+?)\s*$")


@dataclass(frozen=True)
class CatalogRecord:
    path: str
    archive_hash: str
    offset: int
    field3: str
    field4: str
    compressed: bool = False


def _read_raw_sector(stream, sector: int, sector_size: int = 2352) -> bytes:
    stream.seek(sector * sector_size + 24)
    data = stream.read(2048)
    if len(data) != 2048:
        raise ValueError(f"ISO sector {sector} is truncated")
    return data


def _catalog_from_iso(image: Path) -> bytes:
    """Read the root CATDATA file from a raw 2352-byte PSX image."""
    with image.open("rb") as stream:
        pvd = _read_raw_sector(stream, 16)
        if pvd[1:6] != b"CD001":
            raise ValueError(f"{image} does not contain an ISO-9660 primary volume")
        root = pvd[156:190]
        if len(root) < 34 or root[0] < 34:
            raise ValueError(f"{image} has an invalid ISO root directory record")
        root_sector = int.from_bytes(root[2:6], "little")
        root_size = int.from_bytes(root[10:14], "little")
        directory = bytearray()
        remaining = root_size
        sector = root_sector
        while remaining:
            chunk = _read_raw_sector(stream, sector)
            take = min(2048, remaining)
            directory.extend(chunk[:take])
            remaining -= take
            sector += 1

        pos = 0
        while pos < len(directory):
            length = directory[pos]
            if length == 0:
                pos = ((pos // 2048) + 1) * 2048
                continue
            record = directory[pos : pos + length]
            if len(record) < 34:
                break
            name_length = record[32]
            name = record[33 : 33 + name_length].decode("ascii", errors="replace")
            iso_name = name.upper().split(";", 1)[0].rstrip(".")
            if iso_name == "CATDATA":
                file_sector = int.from_bytes(record[2:6], "little")
                file_size = int.from_bytes(record[10:14], "little")
                result = bytearray()
                offset = 0
                while offset < file_size:
                    chunk = _read_raw_sector(stream, file_sector + offset // 2048)
                    result.extend(chunk[: min(2048, file_size - offset)])
                    offset += 2048
                return bytes(result)
            pos += length
    raise FileNotFoundError(f"CATDATA is not present in the root of {image}")


def locate_catalog(source: Path, explicit: Path | None = None) -> tuple[bytes, str]:
    if explicit:
        return explicit.read_bytes(), str(explicit)
    candidates: list[Path] = []
    if source.is_dir():
        candidates.extend(
            [source / "CATDATA", source / "catdata", source / "KAIN2.CATDATA"]
        )
        candidates.extend(sorted(source.glob("*.img")))
        candidates.extend(sorted(source.glob("*.bin")))
    elif source.suffix.lower() in {".img", ".bin"}:
        candidates.append(source)
    else:
        candidates.extend([source.parent / "CATDATA", source.parent / "catdata"])
        candidates.extend(sorted(source.parent.glob("*.img")))
        candidates.extend(sorted(source.parent.glob("*.bin")))
    for candidate in candidates:
        if candidate.name.upper() in {"CATDATA", "KAIN2.CATDATA"} and candidate.is_file():
            return candidate.read_bytes(), str(candidate)
        if candidate.is_file() and candidate.suffix.lower() in {".img", ".bin"}:
            try:
                return _catalog_from_iso(candidate), f"{candidate} (ISO root)"
            except (OSError, ValueError, FileNotFoundError):
                continue
    raise FileNotFoundError(
        "CATDATA was not found. Put CATDATA beside BIGFILE.DAT or provide --catalog."
    )


def parse_catalog(data: bytes) -> list[CatalogRecord]:
    lines = data.decode("latin1").splitlines()
    compressed_paths = {
        match.group(1).replace("\\", "/").lower()
        for line in lines
        if (match := re.match(r'^File\s+"([^"]+)"\s+\(COMPRESSED\)', line))
    }
    records: list[CatalogRecord] = []
    for line in lines:
        match = RECORD_RE.match(line)
        if not match:
            continue
        fields = match.group("fields").split()
        if len(fields) < 4:
            continue
        try:
            offset = int(fields[1], 16)
        except ValueError:
            continue
        path = match.group("path").strip().replace("\\", "/")
        records.append(
            CatalogRecord(
                path=path,
                archive_hash=fields[0],
                offset=offset,
                field3=fields[2],
                field4=fields[3],
                compressed=path.lower() in compressed_paths,
            )
        )
    if not records:
        raise ValueError("CATDATA contains no usable file records")
    if any(left.offset > right.offset for left, right in zip(records, records[1:])):
        raise ValueError("CATDATA file offsets are not monotonic")
    return records


def _find_bigfile(source: Path) -> Path:
    if source.is_file() and source.name.upper() == "BIGFILE.DAT":
        return source
    if source.is_dir():
        for name in ("BIGFILE.DAT", "bigfile.dat"):
            path = source / name
            if path.is_file():
                return path
    raise FileNotFoundError("BIGFILE.DAT was not found in the selected source")


def _matches(record: CatalogRecord, selectors: list[str]) -> bool:
    if not selectors:
        return True
    path = record.path.lower()
    leaf = path.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatch(path, selector.lower().replace("\\", "/").lstrip("/"))
        or fnmatch.fnmatch(path.lstrip("/"), selector.lower().replace("\\", "/").lstrip("/"))
        or fnmatch.fnmatch(leaf, selector.lower())
        for selector in selectors
    )


def _output_relative(record: CatalogRecord, as_pcm: bool) -> Path:
    relative = Path(*record.path.lstrip("/").split("/"))
    # Drop the early build's \kain2\area prefix so the output directory is a
    # direct remaster AREA root (MRLOCK/BIN/...).  Other virtual roots keep a
    # useful game-relative layout after dropping only \kain2.
    if relative.parts and relative.parts[0].lower() == "kain2":
        relative = Path(*relative.parts[1:])
    if relative.parts and relative.parts[0].lower() == "area":
        relative = Path(*relative.parts[1:])
    if as_pcm and relative.suffix.lower() == ".drm":
        relative = relative.with_suffix(".pcm")
    return relative


def extract(
    source: Path,
    output: Path,
    selectors: list[str],
    catalog_path: Path | None = None,
    as_pcm: bool = False,
    limit: int | None = None,
) -> list[dict[str, object]]:
    bigfile = _find_bigfile(source)
    catalog_data, catalog_origin = locate_catalog(source, catalog_path)
    records = parse_catalog(catalog_data)
    selected = [record for record in records if _matches(record, selectors)]
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise FileNotFoundError("No catalog records matched the requested selector")

    source_resolved = bigfile.resolve()
    output_resolved = output.resolve()
    if output_resolved == source_resolved or source_resolved.is_relative_to(output_resolved):
        raise ValueError("output must not contain or overwrite the source archive")

    archive_size = bigfile.stat().st_size
    records_by_offset = sorted(records, key=lambda record: record.offset)
    next_offsets = {id(record): next_record.offset for record, next_record in zip(records_by_offset, records_by_offset[1:])}
    written: list[dict[str, object]] = []
    with bigfile.open("rb") as stream:
        for record in selected:
            end = next_offsets.get(id(record), archive_size)
            if record.offset < 0 or end <= record.offset or end > archive_size:
                raise ValueError(f"invalid archive span for {record.path}")
            stream.seek(record.offset)
            payload = stream.read(end - record.offset)
            destination = output / _output_relative(record, as_pcm)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            written.append(
                {
                    "source_path": record.path,
                    "output_path": destination.as_posix(),
                    "offset": record.offset,
                    "stored_size": len(payload),
                    "compressed": record.compressed,
                    "renamed_drm_to_pcm": as_pcm and record.path.lower().endswith(".drm"),
                }
            )
    manifest = {
        "format": "soul-re-early-bigfile-extract-v1",
        "source": str(bigfile),
        "catalog": catalog_origin,
        "pcm_extension_only": as_pcm,
        "files": written,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "early-extract.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="directory, BIGFILE.DAT, or PSX .img containing the prototype")
    parser.add_argument("--output", type=Path, required=True, help="new staging directory; the source is never modified")
    parser.add_argument("--catalog", type=Path, help="optional CATDATA path when it is not beside the source")
    parser.add_argument("--select", action="append", default=[], help="catalog path/name glob; repeat for multiple files")
    parser.add_argument("--area", help="short-hand selector for an early AREA name, e.g. mrlock")
    parser.add_argument("--pcm", action="store_true", help="rename extracted .drm levels to .pcm (bytes are copied unchanged)")
    parser.add_argument("--limit", type=int, help="limit the number of extracted records")
    args = parser.parse_args(argv)
    selectors = list(args.select)
    if args.area:
        selectors.append(f"*/area/{args.area}/*")
    try:
        files = extract(args.source, args.output, selectors, args.catalog, args.pcm, args.limit)
    except (OSError, ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    print(f"Extracted {len(files)} file(s) to {args.output}")
    for item in files:
        marker = " [compressed bytes]" if item["compressed"] else ""
        print(f"  {item['source_path']} -> {item['output_path']} ({item['stored_size']:,} bytes){marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
