"""
Authentication utilities for MCP endpoints.
"""
from __future__ import annotations

import json
import logging
from typing import Mapping

from werkzeug import Request, Response

from mcp.types import constant_time_compare

logger = logging.getLogger(__name__)


def validate_bearer_token(r: Request, settings: Mapping) -> Response | None:
    """
    Validates the bearer token from the request using constant-time comparison.
    Returns a Response object if validation fails, otherwise None.
    """
    expected_token = settings.get("auth-token")
    if not expected_token:
        return None

    auth_header = r.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        req_id = r.json.get("id") if r.is_json else None
        return Response(
            json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": "Invalid or missing Authorization header"},
            }),
            status=401,
            content_type="application/json",
        )

    token = auth_header.removeprefix("Bearer ").strip()
    if not constant_time_compare(token, expected_token):
        req_id = r.json.get("id") if r.is_json else None
        return Response(
            json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": "Invalid or missing token"},
            }),
            status=401,
            content_type="application/json",
        )

    return None