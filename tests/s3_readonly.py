"""Read-only, network-free S3 listing client for literal path regressions."""

import io

import pytest


class LiteralS3Client:
    """Read-only S3 delimiter listing over exact keys; no AWS session or network."""

    def __init__(self, prefix):
        self.objects = {
            prefix + "report.csv": b"listed report",
            prefix + "child/nested.csv": b"nested report",
        }
        self.calls = []

    def client(self, service, **kwargs):
        assert service == "s3"
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def list_buckets(self, **kwargs):
        self.calls.append(("buckets", kwargs))
        return {"Buckets": [{"Name": "reports-bucket"}]}

    async def list_objects_v2(self, **kwargs):
        self.calls.append(("list", kwargs))
        assert kwargs["Bucket"] == "reports-bucket"
        assert kwargs["Delimiter"] == "/"
        prefix = kwargs["Prefix"]
        directories = set()
        contents = []
        for key, content in self.objects.items():
            if not key.startswith(prefix):
                continue
            suffix = key[len(prefix) :]
            if "/" in suffix:
                directories.add(prefix + suffix.split("/", 1)[0] + "/")
            else:
                contents.append({"Key": key, "Size": len(content)})
        return {
            "CommonPrefixes": [{"Prefix": key} for key in sorted(directories)],
            "Contents": contents,
        }

    async def head_object(self, **kwargs):
        self.calls.append(("head", kwargs))
        assert kwargs["Bucket"] == "reports-bucket"
        assert kwargs["Key"] in self.objects, "read must address the actual listed key"
        return {"ContentLength": len(self.objects[kwargs["Key"]])}

    async def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        assert kwargs["Bucket"] == "reports-bucket"
        body = io.BytesIO(self.objects[kwargs["Key"]])

        class Body:
            async def read(self, size):
                return body.read(size)

        return {"Body": Body()}

    def __getattr__(self, name):
        pytest.fail(f"unexpected client operation: {name}")
