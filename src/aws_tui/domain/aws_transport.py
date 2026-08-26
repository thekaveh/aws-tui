"""Canonical botocore transport failures shared by AWS providers."""

from __future__ import annotations

from botocore.exceptions import (
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    EndpointResolutionError,
    IncompleteReadError,
    ReadTimeoutError,
    ResponseStreamingError,
)
from botocore.exceptions import ConnectionError as BotoConnectionError

AWS_TRANSPORT_EXCEPTIONS = (
    EndpointConnectionError,
    EndpointResolutionError,
    ConnectTimeoutError,
    ReadTimeoutError,
    BotoConnectionError,
    ConnectionClosedError,
    ResponseStreamingError,
    IncompleteReadError,
)

__all__ = ["AWS_TRANSPORT_EXCEPTIONS"]
