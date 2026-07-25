"""Unit tests for SSE wire formatting."""

import json

from app.events import HEARTBEAT_FRAME, format_sse


def test_format_sse_uses_event_type_and_json_data():
    frame = format_sse({"type": "status", "message": "thinking"})

    lines = frame.split("\n")
    assert lines[0] == "event: status"
    assert lines[1].startswith("data: ")
    payload = json.loads(lines[1][len("data: ") :])
    assert payload == {"type": "status", "message": "thinking"}
    # SSE frames terminate with a blank line
    assert frame.endswith("\n\n")


def test_format_sse_defaults_event_name_to_message_when_no_type():
    frame = format_sse({"hello": "world"})
    assert frame.startswith("event: message\n")


def test_heartbeat_frame_is_an_sse_comment():
    # Comment frames keep the connection alive under the ALB idle timeout without
    # being delivered as events to the client.
    assert HEARTBEAT_FRAME.startswith(":")
    assert HEARTBEAT_FRAME.endswith("\n\n")
