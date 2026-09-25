"""Mocked adapter tests: no FirecREST endpoint and no credentials required."""

from __future__ import annotations

from typing import Any

import pytest

import client_v2 as f7t2


class FakeFirecrest:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _call(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def systems(self) -> list[dict[str, str]]:
        self._call("systems")
        return [{"name": "cluster"}]

    def submit(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        self._call("submit", *args, **kwargs)
        return {"jobid": "7"}

    def job_info(self, *args: Any) -> list[dict[str, Any]]:
        self._call("job_info", *args)
        return [{"jobId": "7", "status": {"state": "COMPLETED", "exitCode": 0}}]

    def job_metadata(self, *args: Any) -> list[dict[str, str]]:
        self._call("job_metadata", *args)
        return [
            {
                "jobId": "7",
                "standardOutput": "/out/7.out",
                "standardError": "/out/7.err",
            }
        ]

    def head(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        self._call("head", *args, **kwargs)
        return {"output": "hello-from-fcagent\n"}

    def cancel_job(self, *args: Any) -> dict[str, str]:
        self._call("cancel_job", *args)
        return {}


def config(tmp_path) -> f7t2.FirecRESTConfigV2:
    return f7t2.FirecRESTConfigV2(
        base_url="https://firecrest.test",
        token_url="https://auth.test/token",
        client_id="test-client",
        client_secret="fictional-secret-must-never-appear",
        system="cluster",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
def fake() -> FakeFirecrest:
    return FakeFirecrest()


@pytest.fixture
def client(tmp_path, fake: FakeFirecrest) -> f7t2.FirecRESTClientV2:
    return f7t2.FirecRESTClientV2(config(tmp_path), factory=lambda _: fake)


async def test_adapter_uses_only_the_pyfirecrest_v2_method_surface(
    client: f7t2.FirecRESTClientV2, fake: FakeFirecrest
) -> None:
    assert await client.list_systems() == [{"name": "cluster"}]
    submit = await client.submit_job(
        "#!/bin/bash\necho hello\n", working_directory="/home/demo", partition="debug"
    )
    assert submit.jobid == "7"
    assert (await client.get_job_status("7")).state == "COMPLETED"
    log = await client.get_job_log("7")
    assert log.stdout == "hello-from-fcagent\n"
    assert log.truncated is False
    await client.cancel_job("7")
    assert [call[0] for call in fake.calls] == [
        "systems",
        "submit",
        "job_metadata",
        "job_info",
        "job_metadata",
        "head",
        "head",
        "cancel_job",
    ]
    submit_call = fake.calls[1]
    assert submit_call[2]["script_str"] == "#!/bin/bash\necho hello\n"
    assert submit_call[2]["partition"] == "debug"
    for call in fake.calls[5:7]:
        assert call[2]["num_bytes"] == f7t2.DEFAULT_OUTPUT_BYTES


async def test_adapter_rejects_bad_submission_without_calling_client(
    client: f7t2.FirecRESTClientV2, fake: FakeFirecrest
) -> None:
    with pytest.raises(f7t2.FirecRESTError, match="absolute"):
        await client.submit_job("#!/bin/bash", working_directory=".")
    assert not fake.calls


async def test_adapter_error_does_not_include_exception_payload(tmp_path) -> None:
    secret = "fictional-secret-must-never-appear"

    class Failure:
        def job_info(self, *_: Any) -> list[dict[str, Any]]:
            raise RuntimeError(secret)

    client = f7t2.FirecRESTClientV2(config(tmp_path), factory=lambda _: Failure())
    with pytest.raises(f7t2.FirecRESTError) as raised:
        await client.get_job_status("7")
    assert secret not in str(raised.value)
    assert str(raised.value) == "FirecREST v2 job_info failed"
