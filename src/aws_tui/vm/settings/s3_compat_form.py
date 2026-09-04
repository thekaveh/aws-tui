from __future__ import annotations

from dataclasses import dataclass

from aws_tui.infra.redaction import safe_endpoint_display


@dataclass(frozen=True, slots=True, repr=False)
class S3CompatForm:
    """Inputs collected by the in-app S3-compatible connection form."""

    name: str
    endpoint_url: str
    region: str
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None
    force_path_style: bool = True
    verify_tls: bool = True

    def __repr__(self) -> str:
        masked_id = "***" if self.access_key_id else None
        masked_secret = "***" if self.secret_access_key else None
        masked_token = "***" if self.session_token else None
        return (
            f"S3CompatForm(name={self.name!r}, "
            f"endpoint_url={safe_endpoint_display(self.endpoint_url)!r}, "
            f"region={self.region!r}, access_key_id={masked_id!r}, "
            f"secret_access_key={masked_secret!r}, session_token={masked_token!r}, "
            f"force_path_style={self.force_path_style!r}, verify_tls={self.verify_tls!r})"
        )

    def is_valid(self) -> bool:
        return all(
            (
                self.name.strip(),
                self.endpoint_url.strip(),
                self.region.strip(),
                self.access_key_id.strip(),
                self.secret_access_key.strip(),
            )
        )


__all__ = ["S3CompatForm"]
