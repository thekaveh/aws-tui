"""Lowest-declared-dependency runtime checks.

Run standalone by the ``minimum dependency runtime`` CI job against a
``--resolution lowest-direct`` venv, and also collected by the default suite via
``testpaths``. The package marker keeps both paths importing this module under
one name; see the layout guard in ``tests/docs/test_scaffolding.py``.
"""
