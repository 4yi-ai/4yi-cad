"""Server-Sent Events wire formatting.

The generation loop yields plain dict events; these helpers turn them into SSE
frames. A heartbeat *comment* frame is sent during idle gaps to keep the stream
alive under the ALB idle timeout (300s) without being delivered as an event.
"""

from __future__ import annotations

import json

# Interval (seconds) for idle keepalive; well under the ALB 300s idle timeout.
HEARTBEAT_INTERVAL_S = 15.0

# An SSE comment line — ignored by EventSource, but resets idle timers.
HEARTBEAT_FRAME = ": keepalive\n\n"


def format_sse(event: dict) -> str:
    name = event.get("type", "message")
    return f"event: {name}\ndata: {json.dumps(event)}\n\n"
