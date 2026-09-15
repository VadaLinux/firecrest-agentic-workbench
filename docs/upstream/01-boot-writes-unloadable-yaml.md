# Launcher `/boot` writes a config its own API cannot read back, so guided setup never produces a running FirecREST

## Summary

In the `firecrest-v2-demo` image, `POST /boot` serialises the settings object with
`yaml.dump()`. The object contains `Enum` members, which PyYAML emits as
`!!python/object/apply:` tags. The `firecrest` app then reads that same file back
through `pydantic-settings`' `YamlConfigSettingsSource`, which uses
`yaml.safe_load()` — and `safe_load` refuses those tags. The `firecrest` process
therefore raises `yaml.constructor.ConstructorError` at import time and never
starts.

`/boot` itself returns `200` with a valid access token, because it generates the
token and responds *before* the supervisor-managed restart it requested has had
time to fail. From the wizard UI the setup looks successful; port 5025 is simply
never up.

Net effect: on this revision, completing the demo launcher's own guided setup does
not yield a working FirecREST v2 instance. Manual repair of the generated YAML is
required.

## Environment

| | |
|---|---|
| Image | `ghcr.io/eth-cscs/firecrest-v2-demo:latest` |
| Image digest | `sha256:3dedfc6415191b86386e688938f31c1f1204fb473e0c3318eb92aea2fbd0226d` |
| `org.opencontainers.image.version` | `2.6.0` |
| `org.opencontainers.image.revision` | `dfda5235588e667866db3a756ae6b8c5d0065689` |
| `org.opencontainers.image.created` | `2026-09-08T08:02:18.033Z` |
| `f7t-appversion` response header | `2.6.0` (observed once the API is manually repaired) |
| Host OS | openSUSE Leap 16.0, kernel 6.12.0-160000.37-default |
| Docker | 29.4.0-ce (server and client) |
| Reproduced on | 2026-09-15 |

The target "cluster" was a minimal container running a real `sshd` and real
coreutils, with shell stand-ins for `sbatch`/`squeue`/`sacct`/`scontrol`/`sinfo`.
That substitution is not material to this bug: the crash happens while
`firecrest` parses its own configuration file, before any scheduler or SSH
interaction. `/boot` completed its `sinfo -V` fingerprinting successfully and
reported `slurm 24.11.0, is_compatible: true`.

## Steps to reproduce

1. Start the demo container:

   ```
   docker run -p 8025:8025 -p 5025:5025 -p 3000:3000 \
     ghcr.io/eth-cscs/firecrest-v2-demo:latest
   ```

2. Complete the launcher wizard's three calls against any reachable SSH host with
   a Slurm-compatible `sinfo -V` (the browser UI makes the same calls):

   ```
   curl -s -X POST http://localhost:8025/sshconnection \
     -H "Content-Type: application/json" \
     -d '{"hostname":"<login-node>","hostport":22,"proxyhost":null,"proxyport":null}'

   curl -s -X POST http://localhost:8025/credentials \
     -H "Content-Type: application/json" \
     -d '{"username":"<user>","private_key":"<PEM, \n-escaped>","public_cert":null,"passphrase":null}'

   curl -s -X POST http://localhost:8025/boot \
     -H "Content-Type: application/json" \
     -d '{"cluster_name":"<name>","scheduler_type":"slurm"}'
   ```

3. Check whether the API came up:

   ```
   docker exec <container> supervisorctl -c /etc/supervisord.conf status
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5025/status/systems
   ```

## Expected behaviour

After `/boot` reports success, the `firecrest` program reaches `RUNNING` and
`http://localhost:5025/status/systems` answers (`401` without a token, `200` with
one).

## Observed behaviour

All three wizard calls succeed:

```
### STEP 1: POST /sshconnection
200 {"message":"SSH hosts saved successfully."}
### STEP 2: POST /credentials
200 {"message":"Credentials saved successfully.","user_home":"/home"}
### STEP 3: POST /boot
200 {"message": "Firecrest v2 started successfully.", "access_token": "<REDACTED>",
     "system_name": "fakecluster",
     "scheduler": {"type": "slurm", "version": "24.11.0", "is_compatible": true}}
```

But the `firecrest` program never leaves `STARTING`, and port 5025 does not
answer:

```
$ docker exec <container> supervisorctl -c /etc/supervisord.conf status
firecrest                        STARTING
firecrest-ui                     RUNNING   pid 288, uptime 0:00:01
launcher                         RUNNING   pid 7, uptime 0:01:15

$ curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5025/status/systems
000
```

It is crash-looping under supervisord. The container log shows the same traceback
repeatedly (16 occurrences within the first ~3 minutes after `/boot`):

```
Traceback (most recent call last):
  File "/usr/local/bin/uvicorn", line 8, in <module>
    sys.exit(main())
  ...
  File "/app/firecrest/main.py", line 9, in <module>
    from firecrest.plugins import settings
  File "/app/firecrest/plugins.py", line 10, in <module>
    settings = get_settings()
  File "/app/firecrest/config.py", line 680, in get_settings
    return Settings()
  File "/usr/local/lib/python3.12/site-packages/pydantic_settings/main.py", line 229, in __init__
    else __pydantic_self__.__class__._settings_init_sources(
  File "/usr/local/lib/python3.12/site-packages/pydantic_settings/main.py", line 442, in _settings_init_sources
    sources = cls.settings_customise_sources(
  File "/app/firecrest/config.py", line 667, in settings_customise_sources
    YamlConfigSettingsSource(
  File "/usr/local/lib/python3.12/site-packages/pydantic_settings/sources/providers/yaml.py", line 61, in __init__
    self.yaml_data = self._read_files(self.yaml_file_path, deep_merge=deep_merge)
  File "/usr/local/lib/python3.12/site-packages/pydantic_settings/sources/base.py", line 230, in _read_files
    updating_vars = self._read_file(file_path)
  File "/usr/local/lib/python3.12/site-packages/pydantic_settings/sources/providers/yaml.py", line 84, in _read_file
    return yaml.safe_load(yaml_file) or {}
  File "/usr/local/lib/python3.12/site-packages/yaml/__init__.py", line 125, in safe_load
    return load(stream, SafeLoader)
  ...
  File "/usr/local/lib/python3.12/site-packages/yaml/constructor.py", line 427, in construct_undefined
    raise ConstructorError(None, None,
yaml.constructor.ConstructorError: could not determine a constructor for the tag
'tag:yaml.org,2002:python/object/apply:lib.models.token_endpoint_auth_method.TokenEndpointAuthMethod'
  in "/app/config/app-config.yaml", line 11, column 33
```

### The YAML that `/boot` wrote

`/app/config/app-config.yaml` after `/boot`, four tagged nodes in total
(`grep -n 'python/object'` → lines 11, 38, 41, 44):

```yaml
apis_root_path: ''
app_debug: false
app_version: 2.6.0
auth:
  authentication:
    jwk_algorithm: null
    min_token_ttl: 30
    public_certs:
    - http://localhost:8025/certs
    scopes: {}
    token_endpoint_auth_method: !!python/object/apply:lib.models.token_endpoint_auth_method.TokenEndpointAuthMethod
    - client_secret_basic
    token_url: http://localhost:8025/token
    username_claim: preferred_username
  authorization: null
```

and, further down, the `BackendServiceType` keys of `probing.services` — which
PyYAML is additionally forced to emit in explicit-key (`?`/`:`) form, because the
mapping key is no longer a scalar:

```yaml
  probing:
    interval_check: 120
    services:
      ? !!python/object/apply:firecrest.config.BackendServiceType
      - filesystem
      : timeout: 10
      ? !!python/object/apply:firecrest.config.BackendServiceType
      - scheduler
      : timeout: 10
      ? !!python/object/apply:firecrest.config.BackendServiceType
      - ssh
      : timeout: 10
```

What the loader expects is the plain-scalar form the image ships in
`/app/launcher/f7t-api-config.demo-env.yaml`, i.e. `token_endpoint_auth_method:
client_secret_basic` and `services: {filesystem: {timeout: 10}, ...}`.

## Probable cause

`launcher/main.py:291-294`:

```python
dump: dict[str, Any] = settings.model_dump()
settings_file = os.getenv("YAML_CONFIG_FILE", None)
with open(settings_file, "w") as yaml_file:
    yaml.dump(dump, yaml_file)
```

`settings.model_dump()` (i.e. `mode="python"`) leaves `Enum` members as Enum
objects, and `yaml.dump` uses the full `Dumper`, which represents unknown Python
objects with `!!python/object/apply:` tags. The reader side —
`firecrest/config.py:652-674` → `YamlConfigSettingsSource` → `yaml.safe_load` —
cannot construct them.

Two changes on the writing side each look sufficient on their own:
`settings.model_dump(mode="json")`, which renders Enums as their values, and/or
`yaml.safe_dump(...)`, which fails loudly at write time rather than producing a
file that only breaks later.

## Additional notes

- **The failure is silent from the client's point of view.** `/boot` returns
  `200` and a usable token before the restart it triggers has failed, so the
  wizard reports success. A caller has no signal short of polling 5025.

- **A second call to `/boot` returns `500` instead of `200`**, for an unrelated
  reason in the same handler. `launcher/main.py:303-306` restarts `firecrest-ui`
  guarded only against `RUNNING`:

  ```python
  state = server.supervisor.getProcessInfo("firecrest-ui")
  if state["statename"] == "RUNNING":
      server.supervisor.stopProcess("firecrest-ui")
  server.supervisor.startProcess("firecrest-ui")
  ```

  If `firecrest-ui` is in `STARTING` the stop is skipped and the start raises:

  ```
  File "/app/launcher/main.py", line 306, in boot
  ...
  xmlrpc.client.Fault: <Fault 60: 'ALREADY_STARTED: firecrest-ui'>
  ```

  Observed verbatim on a second `/boot` in the same container:
  `### STEP 3: POST /boot` → `500 Internal Server Error`. The config file was
  still rewritten with the bad tags before the fault, so the first bug applies
  regardless.

- **`yaml.unsafe_load` is not a general workaround** for anyone repairing the
  file by hand, because the tags reference the app's own modules. From a working
  directory other than `/app`:

  ```
  yaml.constructor.ConstructorError: while constructing a Python object
  cannot find module 'lib.models.token_endpoint_auth_method' (No module named 'lib')
    in "/app/config/app-config.yaml", line 11, column 33
  ```

  It succeeds only with `/app` as the working directory.

## Workaround used here

Register a multi-constructor on `SafeLoader` that unwraps any
`!!python/object/apply:` node to its single scalar argument, re-dump the file with
`yaml.safe_dump`, then restart `firecrest` through the same supervisor RPC
interface `/boot` uses (`http://dummy:dummy@localhost:9001/RPC2`). Only the
generated `/app/config/app-config.yaml` is touched; no file shipped in the image
is modified. After that, `GET /status/systems` answers `200` normally.
