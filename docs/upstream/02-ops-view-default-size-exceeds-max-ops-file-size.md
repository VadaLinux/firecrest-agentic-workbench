# `GET /filesystem/{system}/ops/view` always fails when `size` is omitted and `max_ops_file_size` is below 5 MiB

## Summary

`get_view`'s default for the `size` query parameter is hardcoded to `5 * 1024 *
1024`, but the handler validates `size` against
`system.data_operation.max_ops_file_size` *before* that default has any chance of
being reconciled with it. On any system configured with a `max_ops_file_size`
below 5 MiB, calling `/ops/view?path=...` without an explicit `size` therefore
returns `400` unconditionally — including on the `firecrest-v2-demo` image's own
stock cluster config, which ships `max_ops_file_size: 1048576` (1 MiB).

The parameter is optional in the OpenAPI schema, so callers reasonably omit it;
they get a `400` that reads like their own mistake, though they passed no `size`
at all.

## Environment

| | |
|---|---|
| Image | `ghcr.io/eth-cscs/firecrest-v2-demo:latest` |
| Image digest | `sha256:3dedfc6415191b86386e688938f31c1f1204fb473e0c3318eb92aea2fbd0226d` |
| `org.opencontainers.image.version` | `2.6.0` |
| `org.opencontainers.image.revision` | `dfda5235588e667866db3a756ae6b8c5d0065689` |
| `org.opencontainers.image.created` | `2026-09-08T08:02:18.033Z` |
| `f7t-appversion` response header | `2.6.0` |
| Host OS | openSUSE Leap 16.0, kernel 6.12.0-160000.37-default |
| Docker | 29.4.0-ce |
| Reproduced on | 2026-09-15 |

Stock configuration, unmodified — `max_ops_file_size: 1048576` comes from the
image's own `/app/launcher/f7t-api-config.demo-env.yaml:52`:

```yaml
  data_operation:
    max_ops_file_size: 1048576 # 1M
```

and is carried into the generated `/app/config/app-config.yaml:27` by the launcher
wizard.

The filesystem behind these calls was a real `sshd` with real coreutils, so
nothing about this reproduction is stubbed: the `400` comes from FirecREST's own
validation, before any command reaches the remote host.

## Steps to reproduce

1. Bring up `firecrest-v2-demo` and complete the launcher wizard so the API on
   port 5025 is serving.
2. Create any small file on the target system, e.g. `/home/demo/vdlp43.txt`
   containing `hello-from-vdlp43` (18 bytes).
3. Call `/ops/view` **without** `size`:

   ```
   curl -s -D - "http://localhost:5025/filesystem/fakecluster/ops/view?path=/home/demo/vdlp43.txt" \
     -H "Authorization: Bearer ***"
   ```

4. Call it again **with** `size=100`:

   ```
   curl -s -D - "http://localhost:5025/filesystem/fakecluster/ops/view?path=/home/demo/vdlp43.txt&size=100" \
     -H "Authorization: Bearer ***"
   ```

## Expected behaviour

Step 3 returns the file contents. `size` is optional; when it is omitted the
endpoint should read up to whatever the system actually permits — i.e. the
effective default should be `min(5 MiB, max_ops_file_size)`, or the ceiling
itself.

## Observed behaviour

Step 3 — omitting `size` — fails:

```
HTTP/1.1 400 Bad Request
f7t-appversion: 2.6.0

{"errorType":"error","message":"`size` value must be less than 1048576 bytes","causedBy":null,"data":null,"user":"demo"}
```

Step 4 — the identical call with an explicit `size=100` — succeeds, which is what
identifies the default as the cause rather than the file or the path:

```
HTTP/1.1 200 OK
f7t-appversion: 2.6.0

{"output":"hello-from-vdlp43\n"}
```

Two further data points on the boundary:

```
# size=1048576 -- exactly max_ops_file_size
{"output":"hello-from-vdlp43\n"}
HTTP 200

# size=1048575 -- one below
{"output":"hello-from-vdlp43\n"}
HTTP 200
```

So the ceiling itself is accepted (the check is `>`, not `>=`) even though the
error message says "must be less than 1048576 bytes". Minor, but the message and
the check disagree; "must not exceed" would match the code.

## Probable cause

`firecrest/filesystem/ops/router.py:298-333`:

```python
    size: Annotated[
        int | None,
        Query(
            alias="size",
            description="Value, in bytes, of the size of data to be retrieved from the file.",
        ),
    ] = 5
    * 1024
    * 1024,  # Default to 5 MiB
    ...
    if size > system.data_operation.max_ops_file_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"`size` value must be less than {system.data_operation.max_ops_file_size} bytes",
        )
```

The literal default is independent of the per-system ceiling, and the ceiling
check does not distinguish "the caller asked for this" from "we filled this in".
Defaulting to `None` and resolving it as
`size = size or system.data_operation.max_ops_file_size` before the comparison
would keep the explicit-`size` validation intact while making the no-`size` call
work on any configuration.

The same 5 MiB literal appears as the field default in
`firecrest/config.py:361-366`, which is presumably where the 5 MiB figure in the
handler comes from; the two only agree when a deployment leaves
`max_ops_file_size` unset.

## Scope

`get_view` appears to be the only handler in `filesystem/ops/router.py` with a
size default that can exceed the configured ceiling. `get_head`, `get_tail`,
`get_download` and `post_upload` either take no such parameter or compare an
actual content length against `max_ops_file_size` after the fact — e.g.
`/ops/download` of a 2 MiB file on the same 1 MiB configuration returns a
well-formed `413`, which is the correct behaviour and a useful contrast:

```
$ curl -s -o /dev/null -w "HTTP %{http_code}\n" \
    "http://localhost:5025/filesystem/fakecluster/ops/download?path=/home/demo/vdlp43-big.bin" \
    -H "Authorization: Bearer ***"
HTTP 413
```
