"""Test-only raw HTTPS transport for gateway subprocess integration tests."""

from __future__ import annotations

import json
import os
import urllib.request
import base64
from io import BytesIO
from PIL import Image


if os.environ.get("EDITABLE_PPT_RAW_TRANSPORT_FIXTURE") == "1":
    class _Response:
        def __init__(self, payload: bytes): self._payload = payload
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, _limit=-1): return self._payload

    def _qa(payload: dict) -> bytes:
        request = json.loads(payload["input"][0]["content"][0]["text"])
        decision = {
            "status": "complete",
            "checks": {key: {"result": "pass", "detail": "fixture visual pass"} for key in request["check_ids"]},
            "required_image_presence": [{"asset_id": item["asset_id"], "present": True, "detail": "present"} for item in request["required_presence_images"]],
            "required_directive_results": [{"directive_id": item["directive_id"], "satisfied": True, "detail": "satisfied"} for item in request["required_directives"]],
        }
        return json.dumps({"id": "resp_qa_raw_transport", "output": [{"content": [{"type": "output_text", "text": json.dumps(decision)}]}]}).encode()

    def _reconstruction(payload: dict) -> bytes:
        instructions = json.loads(payload["input"][0]["content"][0]["text"])
        text_boxes = []
        coverage = []
        for index, item in enumerate(instructions["authoritative_text"], start=1):
            name = f"body-text-{index:03d}"
            text_boxes.append({"object_id": item["source_id"], "name": name, "text": item["text"], "box_px": [100, 80 + 150 * (index - 1), 1500, 110], "font_size": 16, "font": "Microsoft YaHei", "color": "#111827", "bold": False, "italic": False, "align": "left", "valign": "middle", "wrap": True, "fit_text": True, "z_index": 10 + index})
            coverage.append({"source_id": item["source_id"], "text": item["text"], "object_name": name})
        decision = {"text_boxes": text_boxes, "tables": [], "shapes": [{"object_id": "visual-panel", "name": "visual-panel", "type": "rect", "box_px": [20, 20, 1860, 850], "fill": "#E8EEF4", "stroke": "#E8EEF4", "stroke_width": 0, "z_index": 0}], "images": [], "text_coverage": coverage, "table_coverage": []}
        return json.dumps({"id": "resp_reconstruction_raw_transport", "output": [{"content": [{"type": "output_text", "text": json.dumps(decision)}]}]}).encode()

    def _urlopen(request, timeout=None):
        mode = os.environ.get("EDITABLE_PPT_RAW_TRANSPORT_MODE")
        if mode == "runtime_error": raise RuntimeError("visual backend is offline")
        if mode == "timeout": raise TimeoutError("visual backend timed out")
        payload = json.loads(request.data)
        if "text" not in payload:
            width, height = (int(value) for value in payload["size"].split("x"))
            image = Image.new("RGB", (width, height), "#DCEAF4")
            for x in range(80, width - 40, 180):
                for y in range(80, height - 40, 140):
                    image.paste("#23568C", (x, y, min(x + 90, width), min(y + 50, height)))
            stream = BytesIO(); image.save(stream, format="PNG")
            raw = {"data": [{"b64_json": base64.b64encode(stream.getvalue()).decode("ascii")}]}
            return _Response(json.dumps(raw).encode())
        name = payload["text"]["format"]["name"]
        if mode == "reconstruction_error" and name == "editable_object_manifest":
            raise RuntimeError("reconstruction intentionally pending")
        return _Response(_qa(payload) if name == "page_qa_response" else _reconstruction(payload))

    urllib.request.urlopen = _urlopen
