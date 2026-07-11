"""Smoke test: the docs package imports and pyyaml is available."""


def test_scripts_docs_package_imports():
    import scripts.docs  # noqa: F401


def test_pyyaml_available():
    import yaml

    assert yaml.safe_load("a: 1") == {"a": 1}
