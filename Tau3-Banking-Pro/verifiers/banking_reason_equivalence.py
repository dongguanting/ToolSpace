"""Narrow, opt-in bank-account free-text equivalence for repaired task copies."""


def comparison_hash(environment, task, *, user=False):
    """Hash the complete native DB, normalizing only declared reason aliases.

    Never change the actual environment. Keep ownership, status, balances,
    fees, verification, other records and unknown reason strings byte-strict.
    The native DB type still computes the hash after the explicit normalization.
    """
    raw_hash = environment.get_user_db_hash() if user else environment.get_db_hash()
    rules = task.evaluation_criteria.bank_account_closure_reason_equivalences
    if not rules:
        return raw_hash
    toolkit = environment.user_tools if user else environment.tools
    if toolkit is None or toolkit.db is None:
        return raw_hash
    database = toolkit.db
    state = database.model_dump()
    accounts = state.get("accounts", {}).get("data", {})
    for rule in rules:
        record = accounts.get(rule.account_id)
        if (
            record is not None
            and record.get("user_id") == rule.user_id
            and record.get("status") == "CLOSED"
            and record.get("closure_reason") in rule.accepted_equivalent_reasons
        ):
            record["closure_reason"] = rule.canonical_reason
    return type(database).model_validate(state).get_hash()
