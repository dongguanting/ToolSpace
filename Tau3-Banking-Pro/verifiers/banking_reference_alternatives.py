"""Explicit opt-in banking reference alternatives with complete native state."""

from tau2.evaluator.banking_reason_equivalence import comparison_hash


def matching_alternative(
    constructor, task, predicted, env_kwargs, *, strict_replay=True
):
    """Return a matching label only when both DBs match one complete reference.

    Never normalize financial effects or union individual fields from different
    references. Invalid alternative calls fail visibly rather than being skipped.
    Tasks without this explicit criterion retain the canonical comparison.
    """
    alternatives = task.evaluation_criteria.banking_reference_alternatives or []
    for alternative in alternatives:
        reference = constructor(**env_kwargs)
        initial = task.initial_state
        reference.set_state(
            initialization_data=initial.initialization_data if initial else None,
            initialization_actions=initial.initialization_actions if initial else None,
            message_history=(initial.message_history or []) if initial else [],
            strict=strict_replay,
        )
        for action in alternative.actions:
            result = reference.make_tool_call(
                tool_name=action.name, requestor=action.requestor, **action.arguments
            )
            if str(result).lstrip().lower().startswith(("error:", "failed")):
                raise ValueError(
                    f"Invalid reference alternative {alternative.label}: {action.action_id}: {result}"
                )
        if comparison_hash(reference, task) == comparison_hash(
            predicted, task
        ) and comparison_hash(reference, task, user=True) == comparison_hash(
            predicted, task, user=True
        ):
            return alternative.label
    return None
