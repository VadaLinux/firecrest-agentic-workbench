"""Unit tests for the FirecREST client.

Every test runs against a mocked transport (respx over httpx). No test needs the
demo stack to be running — that is a requirement of prompt 02, because a test
suite that needs a five-minute Slurm build is a test suite nobody runs.

The fixtures below encode the exact request/response shapes observed against the
live demo stack and recorded in docs/hot-cache.md. Where the upstream API is
surprising (the X-Machine-Name header, asynchronous tasks, the sourcePath
parameter on /utilities/download) there is a test pinning the behaviour, so a
future refactor cannot quietly reintroduce the bug.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

import client as f7t

BASE = "http://firecrest.test"
TOKEN_URL = "http://keycloak.test/token"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path):
    return f7t.FirecRESTConfig(
        base_url=BASE,
        token_url=TOKEN_URL,
        client_id="test-client",
        client_secret="test-secret",
        system="cluster",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
async def http():
    # respx patches httpx's transport at the router level, so this client is
    # intercepted by whichever @respx.mock is active.
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
async def client(config, http):
    c = f7t.FirecRESTClient(config, http_client=http)
    yield c
    await c.aclose()


def token_response(expires_in: int = 300) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "jwt-abc", "expires_in": expires_in})


def task_payload(status: str, data=None, description: str = "ok") -> dict:
    return {"task": {"task_id": "t1", "status": status, "data": data,
                     "description": description}}


def mock_token(router: respx.Router, expires_in: int = 300):
    return router.post(TOKEN_URL).mock(return_value=token_response(expires_in))


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


@respx.mock
async def test_token_is_fetched_once_and_reused(client, config):
    tok = mock_token(respx)
    respx.get(f"{BASE}/utilities/whoami").mock(
        return_value=httpx.Response(200, json={"output": "svc-account"})
    )

    await client.whoami()
    await client.whoami()

    assert tok.call_count == 1, "the JWT must be cached, not re-requested per call"


@respx.mock
async def test_token_is_refreshed_when_close_to_expiry(client, monkeypatch):
    # A token that is already inside the refresh margin must not be reused.
    monkeypatch.setattr(f7t, "TOKEN_REFRESH_MARGIN_S", 3600.0)
    tok = mock_token(respx)
    respx.get(f"{BASE}/utilities/whoami").mock(
        return_value=httpx.Response(200, json={"output": "svc-account"})
    )

    await client.whoami()
    await client.whoami()

    assert tok.call_count == 2


@respx.mock
async def test_token_is_never_exposed_to_callers(client):
    mock_token(respx)
    respx.get(f"{BASE}/utilities/whoami").mock(
        return_value=httpx.Response(200, json={"output": "svc-account"})
    )
    assert await client.whoami() == "svc-account"


@respx.mock
async def test_bad_credentials_raise_actionable_error(client):
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(401, json={"error": "invalid_client"})
    )
    with pytest.raises(f7t.FirecRESTError) as exc:
        await client.whoami()
    assert "FIRECREST_CLIENT_ID" in str(exc.value)


@respx.mock
async def test_machine_name_is_sent_as_a_header(client):
    mock_token(respx)
    route = respx.get(f"{BASE}/utilities/whoami").mock(
        return_value=httpx.Response(200, json={"output": "svc-account"})
    )
    await client.whoami()
    assert route.calls[0].request.headers["X-Machine-Name"] == "cluster"
    assert route.calls[0].request.headers["Authorization"] == "Bearer jwt-abc"


@respx.mock
async def test_missing_machine_name_error_explains_the_cause(client):
    mock_token(respx)
    respx.get(f"{BASE}/utilities/whoami").mock(
        return_value=httpx.Response(400, json={"description": "No machine name given"})
    )
    with pytest.raises(f7t.FirecRESTError) as exc:
        await client.whoami()
    assert "X-Machine-Name" in str(exc.value)


# --------------------------------------------------------------------------
# submit_job
# --------------------------------------------------------------------------


@respx.mock
async def test_submit_job_uploads_multipart_and_polls_the_task(client, config):
    mock_token(respx)
    upload = respx.post(f"{BASE}/compute/jobs/upload").mock(
        return_value=httpx.Response(200, json={"success": "Task created", "task_id": "t1"})
    )
    respx.get(f"{BASE}/tasks/t1").mock(
        return_value=httpx.Response(200, json=task_payload("200", {
            "jobid": 42,
            "job_file": "/home/svc/firecrest/abc/job.sh",
            "job_file_out": "/home/svc/firecrest/abc/job.out",
            "job_file_err": "/home/svc/firecrest/abc/job.err",
        }))
    )

    result = await client.submit_job("#!/bin/bash\necho hi\n", "cluster")

    assert result.jobid == "42"
    assert result.job_file_out.endswith("job.out")
    request = upload.calls[0].request
    # The script travels as a multipart file, not as a JSON body, because this
    # API revision answers 405 to POST /compute/jobs.
    assert b'name="file"' in request.content
    assert b"echo hi" in request.content
    assert request.headers["content-type"].startswith("multipart/form-data")


@respx.mock
async def test_submit_job_records_output_paths_for_later(client, config):
    mock_token(respx)
    respx.post(f"{BASE}/compute/jobs/upload").mock(
        return_value=httpx.Response(200, json={"task_id": "t1"})
    )
    respx.get(f"{BASE}/tasks/t1").mock(
        return_value=httpx.Response(200, json=task_payload("200", {
            "jobid": 7,
            "job_file_out": "/out/7.out",
            "job_file_err": "/out/7.err",
        }))
    )

    await client.submit_job("#!/bin/bash\ntrue\n", "cluster")

    recorded = json.loads((config.log_dir / "submissions.json").read_text())
    assert recorded["7"]["job_file_out"] == "/out/7.out"


@respx.mock
async def test_submit_job_waits_for_a_slow_task(client, monkeypatch):
    monkeypatch.setattr(f7t, "TASK_POLL_INTERVAL_S", 0.0)
    mock_token(respx)
    respx.post(f"{BASE}/compute/jobs/upload").mock(
        return_value=httpx.Response(200, json={"task_id": "t1"})
    )
    route = respx.get(f"{BASE}/tasks/t1")
    route.side_effect = [
        httpx.Response(200, json=task_payload("100", description="Queued")),
        httpx.Response(200, json=task_payload("200", {"jobid": 9})),
    ]

    result = await client.submit_job("#!/bin/bash\ntrue\n", "cluster")
    assert result.jobid == "9"
    assert route.call_count == 2


@respx.mock
async def test_failed_task_raises_rather_than_returning_a_jobid(client, monkeypatch):
    monkeypatch.setattr(f7t, "TASK_POLL_INTERVAL_S", 0.0)
    mock_token(respx)
    respx.post(f"{BASE}/compute/jobs/upload").mock(
        return_value=httpx.Response(200, json={"task_id": "t1"})
    )
    respx.get(f"{BASE}/tasks/t1").mock(
        return_value=httpx.Response(200, json=task_payload(
            "400", data="slurm_load_jobs error: Invalid job id specified",
            description="Finished with errors"))
    )

    with pytest.raises(f7t.FirecRESTError) as exc:
        await client.submit_job("#!/bin/bash\ntrue\n", "cluster")
    assert "Invalid job id" in str(exc.value)


# --------------------------------------------------------------------------
# get_job_status
# --------------------------------------------------------------------------


@respx.mock
async def test_get_job_status_uses_acct_not_compute_jobs(client):
    mock_token(respx)
    acct = respx.get(f"{BASE}/compute/acct").mock(
        return_value=httpx.Response(200, json={"task_id": "t2"})
    )
    respx.get(f"{BASE}/tasks/t2").mock(
        return_value=httpx.Response(200, json=task_payload("200", [{
            "jobid": "3", "name": "echo-hello", "state": "COMPLETED",
            "partition": "part01", "exit_code": "0:0", "elapsed_time": "00:00:30",
        }]))
    )

    status = await client.get_job_status("3")

    assert status.state == "COMPLETED"
    assert status.exit_code == "0:0"
    # /compute/acct takes a comma-joined list, per pyfirecrest's own behaviour.
    assert acct.calls[0].request.url.params["jobs"] == "3"


@respx.mock
async def test_get_job_status_rejects_a_missing_record(client):
    mock_token(respx)
    respx.get(f"{BASE}/compute/acct").mock(
        return_value=httpx.Response(200, json={"task_id": "t2"})
    )
    respx.get(f"{BASE}/tasks/t2").mock(
        return_value=httpx.Response(200, json=task_payload("200", []))
    )

    with pytest.raises(f7t.FirecRESTError) as exc:
        await client.get_job_status("999")
    assert "No accounting record" in str(exc.value)


# --------------------------------------------------------------------------
# list_files
# --------------------------------------------------------------------------


@respx.mock
async def test_list_files_uses_target_path(client):
    mock_token(respx)
    route = respx.get(f"{BASE}/utilities/ls").mock(
        return_value=httpx.Response(200, json={"output": [
            {"name": "job.out", "type": "-", "size": "6", "permissions": "rw-r--r--",
             "last_modified": "2026-09-11T15:10:43"},
            {"name": "sub", "type": "d"},
        ]})
    )

    entries = await client.list_files("/home/svc")

    assert [e.name for e in entries] == ["job.out", "sub"]
    assert route.calls[0].request.url.params["targetPath"] == "/home/svc"


# --------------------------------------------------------------------------
# download_file — the upstream parameter inconsistency
# --------------------------------------------------------------------------


@respx.mock
async def test_download_file_sends_source_path(client):
    """Regression test.

    Every other /utilities/* endpoint takes `targetPath`. This one takes
    `sourcePath`, and sending only `targetPath` makes the server raise an
    unhandled TypeError and answer HTTP 500 (src/utilities/utilities.py:739).
    """
    mock_token(respx)
    route = respx.get(f"{BASE}/utilities/download").mock(
        return_value=httpx.Response(200, content=b"hello\n")
    )

    payload = await client.download_file("/home/svc/job.out")

    assert payload == b"hello\n"
    params = route.calls[0].request.url.params
    assert params["sourcePath"] == "/home/svc/job.out"
    assert "targetPath" not in params


@respx.mock
async def test_download_file_raises_on_server_error(client):
    mock_token(respx)
    respx.get(f"{BASE}/utilities/download").mock(return_value=httpx.Response(500))
    with pytest.raises(f7t.FirecRESTError) as exc:
        await client.download_file("/home/svc/job.out")
    assert exc.value.status == 500


# --------------------------------------------------------------------------
# get_job_log
# --------------------------------------------------------------------------


@respx.mock
async def test_get_job_log_reads_both_streams_and_writes_them_locally(client, config):
    mock_token(respx)
    respx.post(f"{BASE}/compute/jobs/upload").mock(
        return_value=httpx.Response(200, json={"task_id": "t1"})
    )
    respx.get(f"{BASE}/tasks/t1").mock(
        return_value=httpx.Response(200, json=task_payload("200", {
            "jobid": 5, "job_file_out": "/out/5.out", "job_file_err": "/out/5.err",
        }))
    )
    respx.get(f"{BASE}/utilities/download").mock(
        side_effect=[
            httpx.Response(200, content=b"printed this\n"),
            httpx.Response(200, content=b"warning: nothing\n"),
        ]
    )

    await client.submit_job("#!/bin/bash\necho hi\n", "cluster")
    log = await client.get_job_log("5")

    assert log.stdout == "printed this\n"
    assert log.stderr == "warning: nothing\n"
    # AGENTS.md: job logs land in logs/, which is what DocMind ingests.
    assert (config.log_dir / "5.stdout.log").read_text() == "printed this\n"
    assert (config.log_dir / "5.stderr.log").read_text() == "warning: nothing\n"


@respx.mock
async def test_get_job_log_explains_when_the_job_is_unknown(client):
    mock_token(respx)
    with pytest.raises(f7t.FirecRESTError) as exc:
        await client.get_job_log("12345")
    assert "submitted through this client" in str(exc.value)


@respx.mock
async def test_get_job_log_keeps_going_when_one_stream_is_unreadable(client, config):
    mock_token(respx)
    respx.post(f"{BASE}/compute/jobs/upload").mock(
        return_value=httpx.Response(200, json={"task_id": "t1"})
    )
    respx.get(f"{BASE}/tasks/t1").mock(
        return_value=httpx.Response(200, json=task_payload("200", {
            "jobid": 6, "job_file_out": "/out/6.out", "job_file_err": "/out/6.err",
        }))
    )
    respx.get(f"{BASE}/utilities/download").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, content=b"boom\n")]
    )

    await client.submit_job("#!/bin/bash\nfalse\n", "cluster")
    log = await client.get_job_log("6")

    assert log.stdout is None
    assert log.stderr == "boom\n"


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_config_from_env_reports_missing_variables(tmp_path, monkeypatch):
    for name in ("FIRECREST_BASE_URL", "FIRECREST_TOKEN_URL",
                 "FIRECREST_CLIENT_ID", "FIRECREST_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("FIRECREST_BASE_URL=http://x\n")

    with pytest.raises(f7t.FirecRESTError) as exc:
        f7t.FirecRESTConfig.from_env(env_file)
    assert "FIRECREST_TOKEN_URL" in str(exc.value) or "FIRECREST_CLIENT_ID" in str(exc.value)


def test_config_from_env_reads_a_dotenv_file(tmp_path, monkeypatch):
    for name in ("FIRECREST_BASE_URL", "FIRECREST_TOKEN_URL",
                 "FIRECREST_CLIENT_ID", "FIRECREST_CLIENT_SECRET", "FIRECREST_SYSTEM"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FIRECREST_BASE_URL=http://kong:8000/\n"
        "FIRECREST_TOKEN_URL=http://kc:8080/token\n"
        "FIRECREST_CLIENT_ID=cid\n"
        "FIRECREST_CLIENT_SECRET=sec\n"
        "FIRECREST_SYSTEM=demo\n"
    )

    config = f7t.FirecRESTConfig.from_env(env_file)

    assert config.base_url == "http://kong:8000"  # trailing slash stripped
    assert config.system == "demo"
