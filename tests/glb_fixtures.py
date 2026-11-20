"""A minimal but valid GLB (a JSON chunk and a BIN chunk), for tests that rewrite one.

Real files come from Blender or a backend; the code that sets `asset.generator` must still
see a well-formed file, so these fixtures are parsed the same way, not faked bytes.
"""

from __future__ import annotations

import json
import struct

GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def valid_glb(*, generator: str = "test exporter", asset_extra: dict | None = None,
              binary: bytes = b"\x00\x00\x00\x00") -> bytes:
    asset = {"version": "2.0", "generator": generator}
    asset.update(asset_extra or {})
    payload = json.dumps({"asset": asset}).encode()
    payload += b" " * ((4 - len(payload) % 4) % 4)
    json_chunk = struct.pack("<II", len(payload), JSON_CHUNK) + payload
    bin_chunk = struct.pack("<II", len(binary), BIN_CHUNK) + binary
    body = json_chunk + bin_chunk
    return struct.pack("<III", GLB_MAGIC, 2, 12 + len(body)) + body


def read_glb(data: bytes) -> tuple[dict, bytes]:
    """(JSON document, BIN chunk) from a GLB."""
    magic, version, _ = struct.unpack_from("<III", data, 0)
    assert (magic, version) == (GLB_MAGIC, 2)
    offset, document, binary = 12, None, b""
    while offset < len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8:offset + 8 + length]
        if kind == JSON_CHUNK:
            document = json.loads(chunk)
        elif kind == BIN_CHUNK:
            binary = chunk
        offset += 8 + length
    return document, binary


def walking_glb(*, seconds: float = 1.0, lift: float = 0.1, fps: int = 30) -> bytes:
    """A two-legged skeleton with one move: each foot lifted once a loop, half a loop apart
    (the left lands at 0.25 of the loop, the right at 0.75), built with the shipped GLB writer.
    Hips with a spine and head, and two legs of thigh, shin, ankle and toe; no mesh."""
    import numpy as np

    from image_to_3dlab.glb_turn import build_glb

    nodes = [{"name": "hips", "translation": [0, 1.0, 0], "children": [1, 3, 7]},
             {"name": "spine", "translation": [0, 0.3, 0], "children": [2]},
             {"name": "head", "translation": [0, 0.4, 0]}]
    for x in (0.1, -0.1):                       # glTF's +X is the model's left
        first = len(nodes)
        nodes += [{"translation": [x, -0.05, 0], "children": [first + 1]},
                  {"translation": [0, -0.45, 0.02], "children": [first + 2]},
                  {"translation": [0, -0.42, -0.02], "children": [first + 3]},
                  {"translation": [0, -0.06, 0.12]}]
    for i, node in enumerate(nodes):
        node.setdefault("name", f"bone_{i}")
    blob, views, accessors = bytearray(), [], []

    def add(values, kind: str) -> int:
        data = np.asarray(values, dtype="<f4").tobytes()
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        blob.extend(data)
        accessors.append({"bufferView": len(views) - 1, "componentType": 5126, "count": len(values), "type": kind})
        return len(accessors) - 1

    count = int(round(seconds * fps)) + 1
    share = np.linspace(0.0, 1.0, count)
    keys = add(share * seconds, "SCALAR")
    samplers, channels = [], []
    for ankle, lands in ((5, 0.25), (9, 0.75)):
        swing = ((share - lands) % 1.0 - 0.6) / 0.4          # down for 0.6 of the loop, then one arc
        up = np.where(swing > 0, lift * np.sin(np.pi * np.clip(swing, 0, 1)), 0.0)
        places = np.tile(nodes[ankle]["translation"], (count, 1)) + np.stack([0 * up, up, 0 * up], axis=1)
        samplers.append({"input": keys, "output": add(places, "VEC3"), "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": ankle, "path": "translation"}})
    document = {"asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}], "nodes": nodes,
                "skins": [{"joints": list(range(len(nodes)))}],
                "animations": [{"name": "walk", "samplers": samplers, "channels": channels}],
                "buffers": [{"byteLength": len(blob)}], "bufferViews": views, "accessors": accessors}
    return build_glb(document, bytes(blob))
