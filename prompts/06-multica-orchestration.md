# Prompt 06 — Wire Multica as the orchestration/board layer

Your task:

1. Self-host Multica (Docker Compose, per its `SELF_HOSTING.md`), or connect the laptop as a runtime to a cloud Multica workspace if self-hosting is too heavy for the laptop on demo day — decide based on available RAM and note the choice in `docs/MULTICA.md` (create it).
2. Add Hermes as an agent in the workspace: pick the runtime you registered, and give it access scoped to this repo/project only.
3. File a test issue in plain language (e.g. "list the files in my home directory on the demo cluster") and assign it to the Hermes agent.
4. Confirm the full loop: agent picks up the issue automatically, comments as it works (using the tools from prompt 05), and moves the issue to review on completion — do not manually intervene.
5. Turn on the execution log for this issue and screenshot/record it for the demo — the tutors will want to see the "what did it actually run" trail, not just the final answer.

## Definition of done

- `docs/MULTICA.md` documents the self-host-vs-cloud decision and the working config.
- The test issue went from filed → picked up → worked → review, with zero manual steps beyond filing it and approving the review.
- The execution log for that issue is saved (screenshot or export) for use in the live demo, as a fallback if live execution has issues on the day.
