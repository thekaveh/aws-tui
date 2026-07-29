"""Amazon Athena service package."""

from aws_tui.services.athena.service import (
    AthenaClientFactory,
    AthenaClientProtocol,
    AthenaService,
    SqlPolicyFactory,
)

__all__ = [
    "AthenaClientFactory",
    "AthenaClientProtocol",
    "AthenaService",
    "SqlPolicyFactory",
]
