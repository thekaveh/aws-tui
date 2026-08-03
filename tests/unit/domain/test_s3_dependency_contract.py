from __future__ import annotations

import botocore.session
import pytest

pytestmark = pytest.mark.unit


def test_s3_operation_models_include_used_conditional_request_members() -> None:
    required_members = {
        "CompleteMultipartUpload": {"IfNoneMatch"},
        "CopyObject": {"CopySourceIfMatch", "IfNoneMatch"},
        "DeleteObject": {"IfMatch", "IfMatchLastModifiedTime", "IfMatchSize"},
        "HeadObject": {"IfMatch"},
        "PutObject": {"IfNoneMatch"},
        "UploadPartCopy": {"CopySourceIfMatch"},
    }
    service = botocore.session.get_session().get_service_model("s3")

    missing: dict[str, list[str]] = {}
    for operation_name, members in required_members.items():
        input_shape = service.operation_model(operation_name).input_shape
        available_members = set(input_shape.members) if input_shape is not None else set()
        if absent := members - available_members:
            missing[operation_name] = sorted(absent)

    assert missing == {}, f"locked botocore S3 models lack required request members: {missing}"
