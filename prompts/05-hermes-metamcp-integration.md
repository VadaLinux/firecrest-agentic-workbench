# Prompt 05 — Point Hermes at MetaMCP and run the end-to-end loop

Your task:

1. Configure Hermes's runtime to use the `firecrest-workbench` MetaMCP endpoint (from prompt 03) as its only MCP tool source for this project. Document the exact config file/flags used in `docs/HERMES.md` (create it).
2. Give Hermes this exact test instruction, verbatim, and record the transcript in `docs/e2e-test-transcript.md`:

   > "Submit the echo-hello job on the demo cluster, wait for it to finish, and tell me what it printed. If you're not sure about the right parameters, check the docs first."

3. Confirm from the transcript that Hermes: checked the hot cache or DocMind before calling `submit_job` (not straight from memory), called `submit_job` and `get_job_status` through MetaMCP, and correctly reported the job's stdout.
4. If Hermes skips the retrieval step, that's a signal to strengthen the tool docstrings from prompt 02, not a signal to add prompt-level instructions forcing it — the docstrings are what other agents/CSCS staff will rely on too.

## Definition of done

- `docs/HERMES.md` has the working config, no secrets.
- `docs/e2e-test-transcript.md` shows a full successful run of the exact test instruction above.
- The run used only MetaMCP-exposed tools — no direct call to `firecrest-mcp` or DocMind's own API from Hermes.
