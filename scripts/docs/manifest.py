"""Parse and validate ``docs/manifest.yaml`` into typed dataclasses.

A section is EITHER a source-leaf (has ``source``) OR a children-group (has
``children``) — never both, never neither (gotcha #14).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class ManifestError(Exception):
    """Raised when the manifest is malformed or references a missing file."""


@dataclass(frozen=True)
class DiagramEntry:
    id: str
    master: str


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    source: str | None = None
    children: tuple[Section, ...] = ()
    diagrams: tuple[str, ...] = ()

    @property
    def is_group(self) -> bool:
        return bool(self.children)


@dataclass(frozen=True)
class Manifest:
    surfaces: tuple[str, ...]
    numbering: str
    sections: tuple[Section, ...]
    diagrams: tuple[DiagramEntry, ...]

    def leaves(self) -> list[Section]:
        out: list[Section] = []
        _collect_leaves(self.sections, out)
        return out


def _collect_leaves(sections: tuple[Section, ...], out: list[Section]) -> None:
    for s in sections:
        if s.is_group:
            _collect_leaves(s.children, out)
        else:
            out.append(s)


def _build_section(raw: dict) -> Section:
    try:
        id_ = raw["id"]
        title = raw["title"]
    except (KeyError, TypeError) as exc:  # TypeError if raw is not a mapping
        raise ManifestError(f"section missing id/title: {raw!r}") from exc
    has_children = "children" in raw and raw["children"]
    has_source = "source" in raw and raw["source"]
    if has_children and has_source:
        raise ManifestError(f"section {id_!r} has both source and children")
    if not has_children and not has_source:
        raise ManifestError(f"section {id_!r} has neither source nor children")
    children = tuple(_build_section(c) for c in raw.get("children", []))
    diagrams = tuple(raw.get("diagrams", []) or ())
    return Section(
        id=id_,
        title=title,
        source=raw.get("source"),
        children=children,
        diagrams=diagrams,
    )


def parse_manifest(text: str) -> Manifest:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a mapping")
    try:
        surfaces = tuple(data["surfaces"])
        numbering = str(data["numbering"])
        sections = tuple(_build_section(s) for s in data["sections"])
        diagrams = tuple(
            DiagramEntry(id=d["id"], master=d["master"]) for d in data.get("diagrams", [])
        )
    except (KeyError, TypeError) as exc:
        raise ManifestError(f"missing/invalid manifest key: {exc}") from exc
    return Manifest(surfaces=surfaces, numbering=numbering, sections=sections, diagrams=diagrams)


def load_manifest(path: str | Path, repo_root: str | Path) -> Manifest:
    path = Path(path)
    repo_root = Path(repo_root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc
    manifest = parse_manifest(text)
    for leaf in manifest.leaves():
        assert leaf.source is not None
        if not (repo_root / leaf.source).is_file():
            raise ManifestError(f"section source not found: {leaf.source}")
    for d in manifest.diagrams:
        if not (repo_root / d.master).is_file():
            raise ManifestError(f"diagram master not found: {d.master}")
    return manifest
