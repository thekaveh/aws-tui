"""Unit tests for the shared service source header."""

from __future__ import annotations

from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.vm.service_source_vm import ServiceSourceContext


def test_source_header_renders_connection_profile_and_region() -> None:
    header = ServiceSourceHeader(ServiceSourceContext("analytics-prod", "prod-sso", "us-west-2"))

    assert header.render().plain == "analytics-prod · prod-sso · us-west-2"
