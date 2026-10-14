"""Guardrail errors with an instruction for the agent on what to do next."""

from __future__ import annotations

TEMPLATE_STEP = (
    "Use an approved template: call list_templates, then prepare_job with "
    "its typed parameters. Free scripts are never accepted."
)
REPREPARE_STEP = (
    "Call prepare_job again to get a new prepared_id, then ask the human to "
    "run `fcagent approve <prepared_id>`."
)

NEXT_STEPS: dict[str, str] = {
    "free_script_not_allowed": TEMPLATE_STEP,
    "template_not_allowed": TEMPLATE_STEP,
    "template_schema_missing": TEMPLATE_STEP,
    "template_parameters_invalid": (
        "Pass exactly the parameters declared by the template "
        "(see list_templates), no more and no fewer."
    ),
    "parameter_invalid": (
        "Fix the named parameter so it matches the template schema "
        "(see list_templates), then call prepare_job again."
    ),
    "max_nodes_exceeded": "Lower `nodes` to the configured limit; prepare again.",
    "max_time_exceeded": "Lower `time_minutes` to the configured limit; prepare again.",
    "partition_not_allowed": "Choose an allowed partition and prepare again.",
    "working_directory_invalid": "Pass an absolute working_directory (starts with /).",
    "prepared_id_required": (
        "Call prepare_job first, wait for `fcagent approve <prepared_id>` by a "
        "human, then call submit_job(prepared_id)."
    ),
    "prepared_not_found": REPREPARE_STEP,
    "prepared_record_invalid": REPREPARE_STEP,
    "approval_pending": (
        "Not approved yet. Ask the human to run `fcagent approve <prepared_id>`, "
        "then call submit_job(prepared_id) again. Do not retry in a loop."
    ),
    "approval_rejected": "The human rejected this job. Do not resubmit unchanged.",
    "approval_expired": REPREPARE_STEP,
    "approval_not_pending": REPREPARE_STEP,
    "approval_signature_invalid": REPREPARE_STEP,
    "approval_key_not_configured": (
        "The approval public key is not configured on this server; "
        "ask the operator to set FCAGENT_APPROVAL_PUBLIC_KEY."
    ),
    "prepared_already_consumed": REPREPARE_STEP,
    "prepared_context_changed": REPREPARE_STEP,
    "reject_reason_invalid": (
        "Use one of: not_intended, wrong_parameters, too_expensive, policy, other."
    ),
    "approval_key_invalid": (
        "The approval key file is missing or unsafe; ask the operator to fix "
        "FCAGENT_APPROVAL_PUBLIC_KEY (absolute path, regular file, safe permissions)."
    ),
    "provenance_not_found": "Check the job id; no provenance card exists for it.",
    "provenance_hash_mismatch": (
        "The template or inputs changed since the original run, so it cannot be "
        "reproduced exactly. Inspect the card with get_provenance."
    ),
}


class GuardrailDenied(ValueError):
    """A request was rejected before rendering, approval or any remote call."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.next_step = NEXT_STEPS.get(
            reason.split(":", 1)[0],
            "Prepare a new job from an approved template.",
        )
