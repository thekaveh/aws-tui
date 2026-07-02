"""aws-tui-side mini-primitives composing VMx artifacts.

Under the round-3 "compose, don't reject" directive (spec §9.bis.11),
each VM need that doesn't have a direct VMx fit is implemented as a
custom aws-tui-side abstraction that COMPOSES the closest VMx
primitive(s) internally + adds the missing behaviour on top, WITHOUT
exposing the primitive in its public surface. These mini-primitives
are the reusable pieces.

Each is also a candidate for VMx vNext to ship natively — see
``docs/superpowers/specs/2026-06-28-vmx-upstream-vnext-asks.md``.
"""

from aws_tui.vm._composition.filtered_composite_vm import FilteredCompositeVM

__all__ = ["FilteredCompositeVM"]
