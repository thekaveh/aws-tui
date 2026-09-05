"""Athena viewmodel unit tests.

This file is load-bearing, not incidental. ``test_page_vm`` is imported by
package path from ``tests/unit/ui/athena/test_page.py``,
``tests/integration/test_athena_page.py``,
``tests/unit/services/athena/test_service.py`` and
``tests/snapshot/apps/athena.py``. Without it pytest ALSO imports the file as a
top-level ``test_page_vm``, so a single session holds two distinct module
objects: the 2.5k-line body runs twice and ``PageClient`` exists as two
unrelated classes, making any cross-boundary ``isinstance`` check silently
false. Every sibling test package carries the same marker.
"""
