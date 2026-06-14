"""S3 service — works for both AWS S3 and S3-compatible endpoints (MinIO, R2, B2, Wasabi)."""

from aws_tui.services.s3.service import S3Service

__all__ = ["S3Service"]
