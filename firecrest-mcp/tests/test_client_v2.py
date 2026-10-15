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

    def job_info(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self._call("job_info", *args, **kwargs)
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
        return {"content": "hello-from-fcagent\n", "contentType": "bytes"}

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


class HTTPStatusError(Exception):
    """Mimics pyfirecrest's HTTP error, which exposes ``responses``."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.responses = [type("Resp", (), {"status_code": status_code})()]


@pytest.mark.parametrize(
    ("status", "hint"),
    [
        # Copied from the upstream test expectations in eth-cscs/firecrest-v2
        # tag 2.6.1, tests/api_response_model_test.py:58 (TimeoutLimitExceeded -> 504)
        # and :68/:73 (SSHClientError/SSHServiceError -> 502).
        (504, "timed out waiting for the cluster"),
        (502, "SSH error while talking to the cluster"),
        (424, "SSH connection to the cluster failed"),
    ],
)
async def test_upstream_ssh_and_timeout_statuses_keep_type_and_add_cause(
    tmp_path, status: int, hint: str
) -> None:
    class Failure:
        def job_info(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
            raise HTTPStatusError(status)

    client = f7t2.FirecRESTClientV2(config(tmp_path), factory=lambda _: Failure())
    with pytest.raises(f7t2.FirecRESTError) as info:
        await client.get_job_status("7")
    assert type(info.value) is f7t2.FirecRESTError
    assert info.value.status == status
    assert hint in str(info.value)


async def test_get_job_status_without_time_window_calls_job_info_unchanged(
    client: f7t2.FirecRESTClientV2, fake: FakeFirecrest
) -> None:
    await client.get_job_status("7")
    name, args, kwargs = fake.calls[-1]
    assert name == "job_info"
    assert args == ("cluster", "7")
    assert kwargs == {}


async def test_get_job_status_forwards_time_window_to_job_info(
    client: f7t2.FirecRESTClientV2, fake: FakeFirecrest
) -> None:
    await client.get_job_status("7", time_window="7d")
    name, args, kwargs = fake.calls[-1]
    assert name == "job_info"
    assert args == ("cluster", "7")
    assert "time_window" in kwargs
    assert kwargs["time_window"] == "7d"
    assert kwargs == {"time_window": "7d"}


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _HttpError(Exception):
    def __init__(self, status_code: int) -> None:
        self.responses = [_Resp(status_code)]


class _NoMetadata(FakeFirecrest):
    def __init__(self, status_code: int) -> None:
        super().__init__()
        self.status_code = status_code

    def job_metadata(self, *args: Any) -> list[dict[str, str]]:
        self._call("job_metadata", *args)
        raise _HttpError(self.status_code)


def _client_with(tmp_path, fake: FakeFirecrest) -> f7t2.FirecRESTClientV2:
    return f7t2.FirecRESTClientV2(config(tmp_path), factory=lambda _: fake)


async def _submit(client: f7t2.FirecRESTClientV2, script: str) -> f7t2.JobSubmit:
    return await client.submit_job(script, working_directory="/home/demo")


async def test_submit_501_gives_null_paths_and_a_note_without_retry(tmp_path) -> None:
    fake = _NoMetadata(501)
    result = await _submit(_client_with(tmp_path, fake), "#!/bin/bash\necho hi\n")
    assert result.job_file_out is None and result.job_file_err is None
    assert result.paths_source is None
    assert "HTTP 501" in (result.note or "")
    assert [c[0] for c in fake.calls].count("job_metadata") == 1  # no retry on 501


async def test_submit_transient_error_also_notes_status_and_does_not_retry(
    tmp_path,
) -> None:
    fake = _NoMetadata(502)
    result = await _submit(_client_with(tmp_path, fake), "#!/bin/bash\n")
    assert "HTTP 502" in (result.note or "")
    assert [c[0] for c in fake.calls].count("job_metadata") == 1


async def test_submit_note_contains_no_secret_or_url(tmp_path) -> None:
    fake = _NoMetadata(501)
    result = await _submit(_client_with(tmp_path, fake), "#!/bin/bash\n")
    text = result.note or ""
    assert "fictional-secret" not in text and "://" not in text


async def test_submit_derives_paths_from_sbatch_with_only_percent_j(tmp_path) -> None:
    script = (
        "#!/bin/bash\n#SBATCH --output=logs/out-%j.txt\n#SBATCH -e /abs/err-%j.txt\n"
    )
    result = await _submit(_client_with(tmp_path, _NoMetadata(501)), script)
    assert result.job_file_out == "/home/demo/logs/out-7.txt"
    assert result.job_file_err == "/abs/err-7.txt"
    assert result.paths_source == "script"
    assert "derived" in (result.note or "")


@pytest.mark.parametrize(
    "pattern", ["%x-%j.out", "%A.out", "%a.out", "%u.out", "100%%.out"]
)
async def test_submit_unsupported_slurm_pattern_leaves_path_null(
    tmp_path, pattern: str
) -> None:
    script = f"#!/bin/bash\n#SBATCH --output={pattern}\n"
    result = await _submit(_client_with(tmp_path, _NoMetadata(501)), script)
    assert result.job_file_out is None and result.paths_source is None
    assert "pattern" in (result.note or "")


async def test_submit_script_without_directives_never_guesses_slurm_default(
    tmp_path,
) -> None:
    result = await _submit(_client_with(tmp_path, _NoMetadata(501)), "#!/bin/bash\n")
    assert result.job_file_out is None
    assert "slurm-" not in (result.note or "")


async def test_scheduler_metadata_wins_over_script(tmp_path, fake) -> None:
    script = "#!/bin/bash\n#SBATCH --output=other-%j.out\n"
    result = await _submit(_client_with(tmp_path, fake), script)
    assert result.job_file_out == "/out/7.out" and result.paths_source == "metadata"
    assert result.note is None


async def test_output_reads_never_call_view_and_head_has_explicit_size(
    tmp_path, fake
) -> None:
    class Spy(FakeFirecrest):
        def view(self, *args: Any, **kwargs: Any) -> str:
            raise AssertionError("view() must never be called (upstream #247)")

        def stat(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self._call("stat", *args)
            return {"size": 3}

        def download(self, *args: Any, **kwargs: Any) -> None:
            self._call("download", *args)
            open(args[2], "wb").write(b"abc")

    spy = Spy()
    client = _client_with(tmp_path, spy)
    await client.get_job_log("7")
    assert await client.download_file("/out/7.out") == b"abc"
    names = [c[0] for c in spy.calls]
    assert "view" not in names and "download" in names
    for call in spy.calls:
        if call[0] == "head":
            assert call[2].get("num_bytes") == f7t2.DEFAULT_OUTPUT_BYTES


async def test_mixed_supported_and_unsupported_stream_keeps_both_messages(
    tmp_path,
) -> None:
    script = "#!/bin/bash\n#SBATCH --output=ok-%j.out\n#SBATCH --error=%x.err\n"
    result = await _submit(_client_with(tmp_path, _NoMetadata(501)), script)
    assert result.job_file_out == "/home/demo/ok-7.out"
    assert result.job_file_err is None and result.paths_source == "script"
    note = result.note or ""
    assert "HTTP 501" in note and "derived" in note and "pattern" in note


async def test_note_never_repeats_exception_text_with_url_or_token(tmp_path) -> None:
    secret = "Bearer eyJfictional.token https://user:pw@auth.test/token"

    class Boom(FakeFirecrest):
        def job_metadata(self, *args: Any) -> list[dict[str, str]]:
            exc = _HttpError(501)
            exc.args = (secret,)
            raise exc

    result = await _submit(_client_with(tmp_path, Boom()), "#!/bin/bash\n")
    note = result.note or ""
    for forbidden in ("Bearer", "eyJ", "://", "pw@", "auth.test"):
        assert forbidden not in note
    assert "HTTP 501" in note


@pytest.mark.parametrize("template", ["hello", "matrix"])
def test_shipped_templates_declare_output_paths_with_only_percent_j(
    template: str,
) -> None:
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[2] / "templates" / f"{template}.sh"
    ).read_text()
    derived = f7t2._sbatch_paths(script, "/home/demo", "42")
    assert derived["out"] == "/home/demo/slurm-42.out"
    assert derived["err"] == "/home/demo/slurm-42.err"
    assert derived["unsupported"] is False
