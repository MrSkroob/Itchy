from pathlib import Path
import xml.etree.ElementTree as ET
import struct


SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3,
    0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB,
    0xCD, 0xCE, 0xCF,
}


def get_jpeg_size(path: str) -> tuple[int, int]:
    with open(path, "rb") as f:
        if f.read(2) != b"\xFF\xD8":
            raise ValueError("Not a JPEG file")

        while True:
            byte = f.read(1)

            if not byte:
                raise ValueError("JPEG has no size information")

            # Find marker prefix
            if byte != b"\xFF":
                continue

            # Skip repeated 0xFF padding bytes
            while byte == b"\xFF":
                byte = f.read(1)

            marker = byte[0]

            if marker in SOF_MARKERS:
                length = struct.unpack(">H", f.read(2))[0]

                _ = f.read(1)
                height, width = struct.unpack(">HH", f.read(4))

                return width, height

            # Markers without a length field
            if marker in {
                0x01,       # TEM
                *range(0xD0, 0xD8),  # restart markers
                0xD8,       # SOI
                0xD9,       # EOI
            }:
                continue

            length_bytes = f.read(2)
            if len(length_bytes) != 2:
                raise ValueError("Invalid JPEG")

            length = struct.unpack(">H", length_bytes)[0]

            # Length includes the two-byte length field itself
            f.seek(length - 2, 1)


def get_png_size(path: str) -> tuple[int, int]:
    with open(path, "rb") as f:
        header = f.read(24)

    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG file")

    width, height = struct.unpack(">II", header[16:24])
    return width, height


def get_svg_size(path: str) -> tuple[float, float]:
    root = ET.parse(path).getroot()

    viewbox = root.get("viewBox")
    if viewbox:
        _, _, width, height = map(float, viewbox.split())
        return width, height

    width = root.get("width")
    height = root.get("height")

    if width is None or height is None:
        raise ValueError("SVG has no size or viewBox")

    width = float(width.removesuffix("px"))
    height = float(height.removesuffix("px"))

    return width, height


def get_image_size(path: Path) -> tuple[float, float]:
    suffix = path.suffix.lower()

    if suffix == ".png":
        return get_png_size(str(path))

    if suffix == ".svg":
        return get_svg_size(str(path))

    if suffix in {".jpg", ".jpeg"}:
        return get_jpeg_size(str(path))

    raise ValueError(f"Unsupported image format: {suffix}")