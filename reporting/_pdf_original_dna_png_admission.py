"""Narrow exact-byte pHYs exception for the already approved original DNA PNG.

The generic raster grammar stays strict. Only the one approved RGB PNG with
its original 3780 px/m pHYs chunk may use this alternate *inspection view*.
The output/resolver receives the original unmodified payload, never the view.
No other ancillary chunk, altered file, new input family or URI is admitted.
"""
from hashlib import sha256
import struct
import zlib

from reporting.png_inspection import _inspect_png_container

ORIGINAL_SHA256 = "596ca17e055b473c5d265ab1865a9adb7e453bf20a001aab79a1a255b36b6437"
RIGHTS_SHA256 = "14a5170bec0e347ec274312f1b915aa9a9d94de61ad32260fc8305f1eb9ddeab"
WIDTH = 324
HEIGHT = 1154
SIZE = 164799
_PHYS_POSITION = 33
_PHYS_END = 54


class OriginalDnaPngError(ValueError):
    """Exact-identity refusal without leaking bytes."""


def inspect_exact_original_dna_png(
    payload: bytes, expected_sha256: str, source_sha256: str,
    rights_sha256: str, width: int, height: int,
) -> dict:
    """Validate the original while retaining its physical-resolution chunk.

    A temporary no-pHYs *inspection input* runs through the unchanged strict
    PNG container and inflated scanline grammar. It is never returned,
    stored, rendered or exposed as a derivative asset.
    """
    try:
        if not (type(payload) is bytes and len(payload) == SIZE
                and type(expected_sha256) is str and expected_sha256 == ORIGINAL_SHA256
                and type(source_sha256) is str and source_sha256 == ORIGINAL_SHA256
                and type(rights_sha256) is str and rights_sha256 == RIGHTS_SHA256
                and type(width) is int and width == WIDTH
                and type(height) is int and height == HEIGHT
                and sha256(payload).hexdigest() == ORIGINAL_SHA256):
            raise OriginalDnaPngError("exact_dna_png_not_admitted")
        # One precisely positioned/identified ancillary and no other chunks.
        chunk = payload[_PHYS_POSITION:_PHYS_END]
        if (chunk[:4] != struct.pack(">I", 9)
                or chunk[4:8] != b"pHYs"
                or chunk[8:17] != struct.pack(">IIB", 3780, 3780, 1)
                or struct.unpack(">I", chunk[17:21])[0]
                    != zlib.crc32(chunk[4:17]) & 0xffffffff):
            raise OriginalDnaPngError("exact_dna_png_not_admitted")
        inspected = payload[:_PHYS_POSITION] + payload[_PHYS_END:]
        metrics = _inspect_png_container(inspected, WIDTH, HEIGHT)
        if not (metrics["chunk_count"] == 3
                and metrics["idat_chunk_count"] == 1
                and metrics["width_px"] == WIDTH
                and metrics["height_px"] == HEIGHT):
            raise OriginalDnaPngError("exact_dna_png_not_admitted")
        # Report ORIGINAL container identity; generic metadata has no chunk claims.
        return {"original_sha256": ORIGINAL_SHA256,
                "original_size_bytes": len(payload),
                "allowed_ancillary_chunk": "pHYs",
                "pixels_decoded": False, "bytes_normalized": False}
    except Exception:
        raise OriginalDnaPngError("exact_dna_png_not_admitted") from None
