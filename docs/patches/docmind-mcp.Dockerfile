# Derived DocMind image that adds the MCP SDK.
#
# Why a derived image instead of adding the dependency upstream or installing at
# container start: DocMind's image ships no MCP server package at all (`mcp` and
# `fastmcp` are both absent — verified), and its venv is the only interpreter that can
# import DocMind's own modules, which the query path needs. Building this on top of
# `docmind:dev` reuses the whole 468-second build, including the baked model cache,
# and adds only the SDK.
#
# Why the scripts are NOT copied in: they are mounted at run time from the compose
# service. A `docker compose up -d` recreates the container, and anything copied with
# `docker cp` or COPY-shot into the image at that point goes stale — the scripts must
# stay editable and survive recreation, because they are the thing being iterated on.
#
# Build:
#     docker build -f docs/patches/docmind-mcp.Dockerfile -t docmind-mcp:dev scripts/
# from the firecrest-agentic-workbench repo root.
FROM docmind:dev

USER root

# Install into DocMind's own venv: that interpreter is the one that sees `src/`.
# Three wrong assumptions live here, each found by failing:
#   - `python -m pip` fails: "No module named pip". DocMind's venv ships without pip.
#   - `uv` is not available either: the image is built with uv in an earlier stage, and
#     uv itself is not carried into the final image ("uv: not found").
#   - `pip` does exist, but at /usr/local/bin/pip, outside the venv; using it would
#     install where DocMind's interpreter cannot see it.
# So: bootstrap pip into the venv with the bundled ensurepip, then install with it.
RUN /app/.venv/bin/python -m ensurepip --upgrade \
 && /app/.venv/bin/python -m pip install --no-cache-dir --no-compile "mcp==2.2.0" \
 && /app/.venv/bin/python -c "import mcp; print('mcp', mcp.__version__ if hasattr(mcp, '__version__') else 'ok')"

# Drop back to the unprivileged user the base image already created.
USER docmind
