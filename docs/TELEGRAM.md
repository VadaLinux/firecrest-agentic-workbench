# Managing the environment from Telegram

## Why

The pitch we make in the demo: a researcher shouldn't need to open a dashboard, remember `curl` syntax, or even open a laptop to check on a job. They should be able to do it from the same app they already use to talk to their team.

Multica supports Telegram as a channel (community-maintained integration, alongside Slack, Lark, DingTalk and WeCom). Once wired, a Telegram chat becomes a second, equally valid way to interact with the board — no separate bot logic to write, it's the same issue/agent/review model as the web UI.

## What it looks like in the demo

1. Researcher sends a message to the bot: *"submit my job.sh on the GPU partition"*.
2. Multica opens an issue from that message and assigns it to the Hermes agent.
3. Hermes works the issue on the laptop runtime (submits via the FirecREST MCP tool, polls status).
4. Progress comments land back in the same Telegram thread — no polling, no refreshing a page.
5. On completion, Multica pings the channel; the researcher approves ("looks good") or asks a follow-up in plain language, which becomes the next issue.

This is the single most demo-able moment for a non-HPC-expert audience: nothing about it looks like infrastructure.

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather), get the bot token.
2. In Multica: **Workspace settings → Channels → Telegram**, paste the token.
3. Bind the channel to the project/workspace that has the Hermes agent and the FirecREST/DocMind tool scope.
4. Add the researcher (and, for the demo, the tutors) as authorized chat members — Multica's role/access-scope model applies here too, so the Telegram channel doesn't bypass the same permission boundaries as the web board.
5. Test the round trip before the demo: file a trivial issue from Telegram, confirm it appears on the web board and vice versa.

## Caveat to state plainly during the pitch

The Telegram channel is community-maintained in Multica, not a first-party guarantee. For the hackathon demo this is a non-issue (we control the whole stack); worth flagging explicitly if CSCS considers this path for anything beyond a demo, since long-term support depends on the community package staying maintained.
