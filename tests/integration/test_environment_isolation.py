from __future__ import annotations

import os
from pathlib import Path


def test_integration_tests_use_isolated_aws_files(tmp_path: Path) -> None:
    for variable in (
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "BOTO_CONFIG",
    ):
        configured = Path(os.environ[variable])
        assert configured.parent == tmp_path
        assert configured.read_text(encoding="utf-8") == ""

    assert os.environ["AWS_EC2_METADATA_DISABLED"] == "true"
