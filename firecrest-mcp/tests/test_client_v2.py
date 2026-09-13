"""Unit tests for the FirecREST v2 client.

Every test runs against a mocked transport (respx over httpx). No test needs
either demo stack running — same rule as test_client.py, now doubly true
since AGENTS.md requires pytest to pass with both stacks stopped.

The fixtures below encode the exact request/response shapes observed against
the live v2 demo (`ghcr.io/eth-cscs/firecrest-v2-demo:latest`, app_version
2.6.0) and recorded in docs/hot-cache-v2.md. Where v2 differs from v1 —
URL-path addressing, no task polling, the `path` parameter rename, the
explicit `size` on ops/view — there is a regression test pinning it, mirroring
how test_client.py pins v1's own quirks.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

import client as f7t
import client_v2 as f7t2

BASE = "http://firecrest-v2.test"
TOKEN_URL = "http://launcher.test/token"
SYSTEM = "fakecluster"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path):
    return f7t2.FirecRESTConfigV2(
        base_url=BASE,
        token_url=TOKEN_URL,
        client_id="test-client",
        client_secret="test-secret",
        system=SYSTEM,
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
async def client(config, http):
    c = f7t2.FirecRESTClientV2(config, http_client=http)
    yield c
    await c.aclose()


def token_response(expires_in: int = 31556952) -> httpx.Response:
    # v2 demo launcher issues ~1-year tokens; the client must not assume a
    # particular TTL either way (docs/hot-cache-v2.md §1).
    return httpx.Response(200, json={"access_token": "jwt-v2-abc", "expires_in": expires_in})


def mock_token(router: respx.Router, expires_in: int = 31556952):
    return router.post(TOKEN_URL).mock(return_value=token_response(expires_in))


def job_status_payload(**overrides) -> dict:
    entry = {
        "jobId": "1",
        "name": "echo-hello-v2",
        "status": {"state": "COMPLETED", "stateReason": "None", "exitCode": 0,
                    "interruptSignal": 0},
        "tasks": [],
        "time": {"elapsed": 1, "start": 1789228045, "end": 1789228045,
                  "suspended": 0, "limit": None},
        "account": "demo", "allocationNodes": 1, "cluster": "fakecluster",
        "group": "demo", "nodes": "fakenode0", "partition": "debug",
        "killRequestUser": None, "user": "demo", "workingDirectory": "/home/demo",
        "priority": 1,
    }
    entry.update(overrides)
    return {"jobs": [entry]}


def job_metadata_payload(**overrides) -> dict:
    entry = {
        "jobId": "1", "script": "#!/bin/bash\necho hi\n",
        "standardInput": "/dev/null", "standardOutput": "/home/demo/echo-hello.out",
        "standardError": "/home/demo/echo-hello.err",
    }
    entry.update(overrides)
    return {"jobs": [entry]}


def error_envelope(message: str) -> dict:
    return {"errorType": "error", "message": message, "causedBy": None,
             "data": None, "user": "demo"}


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


@respx.mock
async def test_token_is_fetched_once_and_reused(client):
    tok = mock_token(respx)
    respx.get(f"{BASE}/filesystem/{SYSTEM}/ops/ls").mock(
        return_value=httpx.Response(200, json={"output": []})
    )

    await client.list_files("/home/demo")
    await client.list_files("/home/demo")

    assert tok.call_count == 1, "the JWT must be cached, not re-requested per call"


@respx.mock
async def test_token_is_refreshed_when_close_to_expiry(client, monkeypatch):
    monkeypatch.setattr(f7t2, "TOKEN_REFRESH_MARGIN_S", 10 ** 9)
    tok = mock_token(respx)
    respx.get(f"{BASE}/filesystem/{SYSTEM}/ops/ls").mock(
        return_value=httpx.Response(200, json={"output": []})
    )

    await client.list_files("/home/demo")
    await client.list_files("/home/demo")

    assert tok.call_count == 2


@respx.mock
async def test_bad_credentials_raise_actionable_error(client):
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(401, json={"error": "invalid_client"})
    )
    with pytest.raises(f7t2.FirecRESTError) as exc:
        await client.list_files("/home/demo")
    assert "FIRECREST_V2_CLIENT_ID" in str(exc.value)


@respx.mock
async def test_system_is_a_url_path_segment_not_a_header(client):
    """Regression: v2 has no X-Machine-Name at all (docs/hot-cache-v2.md §0)."""
    mock_token(respx)
    route = respx.get(f"{BASE}/filesystem/{SYSTEM}/ops/ls").mock(
        return_value=httpx.Response(200, json={"output": []})
    )
    await client.list_files("/home/demo")
    request = route.calls[0].request
    assert SYSTEM in str(request.url)
    assert "X-Machine-Name" not in request.headers
    assert request.headers["Authorization"] == "Bearer jwt-v2-abc"


@respx.mock
async def test_fircrest_error_is_the_same_type_across_client_and_client_v2():
    """The agent should never need to know which version raised an error."""
    assert f7t2.FirecRESTError is f7t.FirecRESTError


# --------------------------------------------------------------------------
# submit_job
# --------------------------------------------------------------------------


@respx.mock
async def test_submit_job_sends_json_not_multipart(client):
    """Regression: v2 has no multipart upload route at all (§4)."""
    mock_token(respx)
    submit = respx.post(f"{BASE}/compute/{SYSTEM}/jobs").mock(
        return_value=httpx.Response(200, json={"jobId": "1"})
    )
    respx.get(f"{BASE}/compute/{SYSTEM}/jobs/1/metadata").mock(
        return_value=httpx.Response(200, json=job_metadata_payload())
    )

    result = await client.submit_job(
        "#!/bin/bash\necho hi\n", SYSTEM, working_directory="/home/demo"
    )

    assert result.jobid == "1"
    assert result.job_file_out == "/home/demo/echo-hello.out"
    assert result.job_file_err == "/home/demo/echo-hello.err"
    request = submit.calls[0].request
    assert request.headers["content-type"] == "application/json"
    payload = json.loads(request.content)
    assert payload["job"]["script"] == "#!/bin/bash\necho hi\n"
    assert payload["job"]["workingDirectory"] == "/home/demo"
    assert "scriptPath" not in payload["job"]


@respx.mock
async def test_submit_job_with_script_path_instead_of_inline_script(client):
    mock_token(respx)
    respx.post(f"{BASE}/compute/{SYSTEM}/jobs").mock(
        return_value=httpx.Response(200, json={"jobId": "2"})
    )
    respx.get(f"{BASE}/compute/{SYSTEM}/jobs/2/metadata").mock(
        return_value=httpx.Response(200, json=job_metadata_payload(jobId="2"))
    )

    result = await client.submit_job(
        system=SYSTEM, script_path="/home/demo/job.sh", working_directory="/home/demo"
    )

    assert result.job_file == "/home/demo/job.sh"


async def test_submit_job_requires_script_or_script_path(client):
    with pytest.raises(f7t2.FirecRESTError):
        await client.submit_job(system=SYSTEM, working_directory="/home/demo")


async def test_submit_job_rejects_both_script_and_script_path(client):
    with pytest.raises(f7t2.FirecRESTError):
        await client.submit_job(
            "#!/bin/bash\necho hi\n", system=SYSTEM, script_path="/home/demo/job.sh",
            working_directory="/home/demo",
        )


async def test_submit_job_rejects_a_missing_working_directory(client):
    with pytest.raises(f7t2.FirecRESTError) as exc:
        await client.submit_job("#!/bin/bash\necho hi\n", system=SYSTEM)
    assert "absolute" in str(exc.value)


async def test_submit_job_rejects_a_relative_working_directory(client):
    """Regression for a finding made verifying this issue: a relative value

    like "." is accepted by POST /compute/{system}/jobs and the job really
    does run, but v2's own status/metadata responses echo it back unresolved
    and ops/view / ops/download both reject non-absolute paths — so a job
    submitted this way would be real but permanently unreadable through this
    client. Reject it here rather than submit it.
    """
    with pytest.raises(f7t2.FirecRESTError) as exc:
        await client.submit_job(
            "#!/bin/bash\necho hi\n", system=SYSTEM, working_directory="."
        )
    assert "absolute" in str(exc.value)


@respx.mock
async def test_submit_job_survives_a_failed_metadata_lookup(client):
    """Submission itself succeeded; the paths-for-free read is best-effort."""
    mock_token(respx)
    respx.post(f"{BASE}/compute/{SYSTEM}/jobs").mock(
        return_value=httpx.Response(200, json={"jobId": "3"})
    )
    respx.get(f"{BASE}/compute/{SYSTEM}/jobs/3/metadata").mock(
        return_value=httpx.Response(404, json=error_envelope("Job not found."))
    )

    result = await client.submit_job(
        "#!/bin/bash\ntrue\n", SYSTEM, working_directory="/home/demo"
    )

    assert result.jobid == "3"
    assert result.job_file_out is None


# --------------------------------------------------------------------------
# get_job_status
# --------------------------------------------------------------------------


@respx.mock
async def test_get_job_status_is_a_single_call_with_no_task_polling(client):
    """Regression: no /tasks/* indirection exists in v2 at all (§0, §4)."""
    mock_token(respx)
    route = respx.get(f"{BASE}/compute/{SYSTEM}/jobs/1").mock(
        return_value=httpx.Response(200, json=job_status_payload())
    )

    status = await client.get_job_status("1")

    assert status.state == "COMPLETED"
    assert status.exit_code == 0
    assert status.partition == "debug"
    assert route.call_count == 1


@respx.mock
async def test_get_job_status_rejects_a_missing_record(client):
    mock_token(respx)
    respx.get(f"{BASE}/compute/{SYSTEM}/jobs/999").mock(
        return_value=httpx.Response(404, json=error_envelope("Job not found."))
    )
    with pytest.raises(f7t2.FirecRESTError) as exc:
        await client.get_job_status("999")
    assert "Job not found" in str(exc.value)


# --------------------------------------------------------------------------
# list_files
# --------------------------------------------------------------------------


@respx.mock
async def test_list_files_uses_path_not_target_path(client):
    """Regression: v2 renamed v1's targetPath to path (§3)."""
    mock_token(respx)
    route = respx.get(f"{BASE}/filesystem/{SYSTEM}/ops/ls").mock(
        return_value=httpx.Response(200, json={"output": [
            {"name": "job.out", "type": "-", "size": "6", "permissions": "rw-r--r--.",
             "lastModified": "2026-09-12T15:47:22", "user": "demo", "group": "demo",
             "linkTarget": None},
        ]})
    )

    entries = await client.list_files("/home/demo")

    assert [e.name for e in entries] == ["job.out"]
    assert entries[0].last_modified == "2026-09-12T15:47:22"
    params = route.calls[0].request.url.params
    assert params["path"] == "/home/demo"
    assert "targetPath" not in params


# --------------------------------------------------------------------------
# download_file
# --------------------------------------------------------------------------


@respx.mock
async def test_download_file_uses_path_param(client):
    mock_token(respx)
    route = respx.get(f"{BASE}/filesystem/{SYSTEM}/ops/download").mock(
        return_value=httpx.Response(200, content=b"hello\n")
    )

    payload = await client.download_file("/home/demo/job.out")

    assert payload == b"hello\n"
    assert route.calls[0].request.url.params["path"] == "/home/demo/job.out"


@respx.mock
async def test_download_file_raises_on_server_error(client):
    mock_token(respx)
    respx.get(f"{BASE}/filesystem/{SYSTEM}/ops/download").mock(
        return_value=httpx.Response(404, json=error_envelope("File not found"))
    )
    with pytest.raises(f7t2.FirecRESTError) as exc:
        await client.download_file("/home/demo/missing.out")
    assert exc.value.status == 404


# --------------------------------------------------------------------------
# get_job_log
# --------------------------------------------------------------------------


@respx.mock
async def test_get_job_log_uses_metadata_then_views_both_streams(client, config):
    mock_token(respx)
    respx.get(f"{BASE}/compute/{SYSTEM}/jobs/5/metadata").mock(
        return_value=httpx.Response(200, json=job_metadata_payload(
            jobId="5", standardOutput="/out/5.out", standardError="/out/5.err"))
    )
    view = respx.get(f"{BASE}/filesystem/{SYSTEM}/ops/view").mock(side_effect=[
        httpx.Response(200, json={"output": "printed this\n"}),
        httpx.Response(200, json={"output": "warning: nothing\n"}),
    ])

    log = await client.get_job_log("5")

    assert log.stdout == "printed this\n"
    assert log.stderr == "warning: nothing\n"
    # AGENTS.md: job logs land in logs/, prefixed v2- so ids never collide
    # with a v1 job of the same number.
    assert (config.log_dir / "v2-5.stdout.log").read_text() == "printed this\n"
    assert (config.log_dir / "v2-5.stderr.log").read_text() == "warning: nothing\n"
    # Regression: ops/view must never be called without an explicit size —
    # its own default exceeds the v2 demo's own configured ceiling (§3).
    for call in view.calls:
        assert "size" in call.request.url.params


@respx.mock
async def test_get_job_log_explains_when_the_job_is_unknown(client):
    mock_token(respx)
    respx.get(f"{BASE}/compute/{SYSTEM}/jobs/12345/metadata").mock(
        return_value=httpx.Response(404, json=error_envelope("Job not found."))
    )
    with pytest.raises(f7t2.FirecRESTError) as exc:
        await client.get_job_log("12345")
    assert "12345" in str(exc.value)


@respx.mock
async def test_get_job_log_keeps_going_when_one_stream_is_unreadable(client, config):
    mock_token(respx)
    respx.get(f"{BASE}/compute/{SYSTEM}/jobs/6/metadata").mock(
        return_value=httpx.Response(200, json=job_metadata_payload(
            jobId="6", standardOutput="/out/6.out", standardError="/out/6.err"))
    )
    respx.get(f"{BASE}/filesystem/{SYSTEM}/ops/view").mock(side_effect=[
        httpx.Response(404, json=error_envelope("No such file")),
        httpx.Response(200, json={"output": "boom\n"}),
    ])

    log = await client.get_job_log("6")

    assert log.stdout is None
    assert log.stderr == "boom\n"


# --------------------------------------------------------------------------
# Error envelope
# --------------------------------------------------------------------------


@respx.mock
async def test_error_envelope_is_parsed_uniformly(client):
    """v2 has one consistent error shape everywhere, unlike v1's ad hoc ones (§5)."""
    mock_token(respx)
    respx.get(f"{BASE}/filesystem/bogus-system/ops/ls").mock(
        return_value=httpx.Response(404, json=error_envelope("System not found"))
    )
    with pytest.raises(f7t2.FirecRESTError) as exc:
        await client.list_files("/home", system="bogus-system")
    assert exc.value.status == 404
    assert "System not found" in str(exc.value)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_config_from_env_reports_missing_variables(tmp_path, monkeypatch):
    for name in ("FIRECREST_V2_BASE_URL", "FIRECREST_V2_TOKEN_URL",
                 "FIRECREST_V2_CLIENT_ID", "FIRECREST_V2_CLIENT_SECRET",
                 "FIRECREST_V2_SYSTEM"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("FIRECREST_V2_BASE_URL=http://x\n")

    with pytest.raises(f7t2.FirecRESTError) as exc:
        f7t2.FirecRESTConfigV2.from_env(env_file)
    assert "FIRECREST_V2_" in str(exc.value)


def test_config_from_env_reads_a_dotenv_file(tmp_path, monkeypatch):
    for name in ("FIRECREST_V2_BASE_URL", "FIRECREST_V2_TOKEN_URL",
                 "FIRECREST_V2_CLIENT_ID", "FIRECREST_V2_CLIENT_SECRET",
                 "FIRECREST_V2_SYSTEM"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FIRECREST_V2_BASE_URL=http://firecrest:5025/\n"
        "FIRECREST_V2_TOKEN_URL=http://launcher:8025/token\n"
        "FIRECREST_V2_CLIENT_ID=cid\n"
        "FIRECREST_V2_CLIENT_SECRET=sec\n"
        "FIRECREST_V2_SYSTEM=fakecluster\n"
    )

    config = f7t2.FirecRESTConfigV2.from_env(env_file)

    assert config.base_url == "http://firecrest:5025"  # trailing slash stripped
    assert config.system == "fakecluster"


def test_config_v2_and_v1_env_vars_do_not_collide(tmp_path, monkeypatch):
    """The two config namespaces must be independently settable in one .env."""
    for name in ("FIRECREST_BASE_URL", "FIRECREST_TOKEN_URL", "FIRECREST_CLIENT_ID",
                 "FIRECREST_CLIENT_SECRET", "FIRECREST_V2_BASE_URL",
                 "FIRECREST_V2_TOKEN_URL", "FIRECREST_V2_CLIENT_ID",
                 "FIRECREST_V2_CLIENT_SECRET", "FIRECREST_V2_SYSTEM"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FIRECREST_BASE_URL=http://kong:8000\n"
        "FIRECREST_TOKEN_URL=http://kc:8080/token\n"
        "FIRECREST_CLIENT_ID=v1cid\n"
        "FIRECREST_CLIENT_SECRET=v1sec\n"
        "FIRECREST_V2_BASE_URL=http://firecrest:5025\n"
        "FIRECREST_V2_TOKEN_URL=http://launcher:8025/token\n"
        "FIRECREST_V2_CLIENT_ID=v2cid\n"
        "FIRECREST_V2_CLIENT_SECRET=v2sec\n"
        "FIRECREST_V2_SYSTEM=fakecluster\n"
    )

    v1_config = f7t.FirecRESTConfig.from_env(env_file)
    v2_config = f7t2.FirecRESTConfigV2.from_env(env_file)

    assert v1_config.base_url == "http://kong:8000"
    assert v2_config.base_url == "http://firecrest:5025"
    assert v1_config.client_id == "v1cid"
    assert v2_config.client_id == "v2cid"
