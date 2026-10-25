"""Validate one user-entered basename without changing its spelling."""

from unicodedata import category

from aws_tui.domain.filesystem import ProviderError


def require_entry_name(name: str) -> None:
    if not name.strip():
        raise ProviderError("name must not be empty")
    if (
        name in {".", ".."}
        or "/" in name
        or "\\" in name
        or any(category(character) == "Cc" for character in name)
    ):
        raise ProviderError("name must be a single path segment without control characters")
