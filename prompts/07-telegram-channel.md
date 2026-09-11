# Prompt 07 — Wire the Telegram channel

Follow `docs/TELEGRAM.md` in this repo for the full setup procedure — this prompt is just the execution checklist.

Your task:

1. Create the bot via @BotFather, get the token (do not commit it — store it wherever Multica's channel config expects, following its own docs).
2. Bind the bot to the same workspace/project as the Hermes agent from prompt 06.
3. From a phone (or Telegram desktop client, for rehearsal), send the same test instruction used in prompt 05: "submit the echo-hello job on the demo cluster and tell me what it printed."
4. Confirm the issue appears on the Multica web board, the agent works it exactly as it did in prompt 05, and the result comes back in the Telegram thread — not just "check the web UI."
5. Rehearse this exact sequence at least twice before the hackathon; live bot/network hiccups during the actual demo are the most likely failure point, so have the `docs/e2e-test-transcript.md` from prompt 05 ready as a fallback screen-share if Telegram misbehaves live.

## Definition of done

- A message sent from Telegram results in a completed, reviewed issue with the result visible back in the same Telegram thread, with no manual step on the web board.
- The sequence has been rehearsed at least twice successfully.
- A fallback (recorded transcript or screenshots) exists in case live Telegram access is unreliable at the venue.
