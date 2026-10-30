"""Original default task-generation prompts, extracted without optional experiment flags."""

FULL_WORKFLOW_MAX_WORDS = 250

FULL_WORKFLOW_MAX_ASSERTIONS = 12

QUESTION_PRESENTATION_POLICY = {
    "language": "English",
    "format": "continuous_prose",
    "contexts": ["business_email", "work_ticket", "business_request"],
    "headings_allowed": False,
    "bullets_allowed": False,
    "numbered_steps_allowed": False,
    "scope": "future_generation_only",
}

QUESTION_DIFFICULTY_POLICY = {
    "version": "grounded_decisions_v2",
    "scope": "future_full_workflow_generation_only",
    "minimum_evidence_chains": 2,
    "minimum_cross_source_chains": 1,
    "minimum_distinct_scored_decision_assertions": 2,
    "answer_leakage_allowed": False,
    "outcome_overrides_allowed": False,
    "independent_objectives_allowed": True,
    "random_walk_changes": False,
    "initial_query_evidence_audit": True,
}

PLANNER_SYSTEM = """You are a stateful-task design auditor for AutomationBench.

The input contains the official source task's user prompt, endpoint definitions, one complete and
untruncated trajectory that was actually executed and replayed successfully, and deterministic state
differences derived from its initial and final WorldStates. Tool names are endpoint_id values; there
is no api_fetch tool. Treat the official source user prompt as the authoritative pre-execution
business contract when its rules match the trajectory. Do not treat source assertions as facts.

Decide whether the trajectory represents one natural, coherent, executable user goal and produce a
structured task plan. Requirements:
1. Every state-changing step must be an explicit business action requested by the user, never filler.
2. Queries must belong to the scenario and collectively support discovery or verification. A
   redundant, empty, or superseded read-only search is not by itself a rejection reason: the new task
   is scored on final state and does not require an agent to reproduce the reference query sequence.
3. All steps must belong to one business scenario. Reject unrelated paths.
   Keep only source-request categories that this trajectory actually investigates or acts on; do not
   carry an unused category into the new plan merely because it appears in the source prompt.
4. You are designing a new task, so user-known rules may come from the official source user prompt or
   from stable control values in mutation arguments. Consistent destination, due date, status,
   priority, relationship, and category-to-field mappings may be promoted into explicit constraints
   of the new question even when the source prompt omitted them; this is a task-design choice, not an
   invented external policy. Observations remain post-execution evidence and must not be presented as
   facts the user already knows.
   A value that first appears in a query observation, or is derived from one, remains discovered
   evidence when it is copied into a later mutation argument. The later argument does not turn it into
   a user-known rule. Stable control values such as destination, status, due date, and priority may be
   extracted from mutation arguments when they are not query results.
5. Every scored business condition must be entailed by the eventual user request.
6. Extract an explicit field-level write contract for each mutation. Stable arguments such as
   destination, relationship, date, status, priority, category, and conditional value mappings must
   be stated clearly in the eventual request. Do not collapse an exact mapping into phrases such as
   "according to strength" or "as appropriate".
   Also extract target_scope from explicit names, IDs, or filters in the source request and query
   arguments. A finite reference path must not become an unbounded request to process every record.
7. Separate new-task control rules from evidence that the agent must discover. Classify a value
   by its earliest appearance in the trajectory, not by its final use. Never invent a
   qualification threshold that is absent from the source request, stable control arguments, and any
   authoritative record queried by the trajectory. Reject only when the state-changing actions cannot
   be expressed coherently without treating a discovered outcome as a pre-execution rule.
8. step_coverage must cover every trajectory step exactly once, using one-based step numbers.
9. Every natural-language string in the output must be in English.

Return JSON only:
{
  "acceptable": true,
  "rejection_reason": "",
  "scenario": "one coherent business scenario",
  "user_known_inputs": ["real input conditions that may appear in the request"],
  "target_scope": ["explicit entity, filter, or bounded population to process"],
  "required_actions": [
    {"step": 2, "endpoint_id": "service.operation", "intent": "user-requested action"}
  ],
  "information_goals": [
    {"steps": [1, 3], "purpose": "why these queries are necessary"}
  ],
  "decision_rules": [
    {
      "condition": "business condition",
      "result": "unambiguous action or field value",
      "source": "real call arguments or queried authoritative record"
    }
  ],
  "write_contracts": [
    {
      "step": 2,
      "stable_fields": {"status": "exact required value"},
      "free_text_facts": {"subject": ["facts it must identify"], "description": ["facts to include"]}
    }
  ],
  "unresolved_ambiguities": [],
  "requested_output": {"format": "brief confirmation", "fields": []},
  "step_coverage": [{"step": 1, "purpose": "why this call belongs to the task"}]
}

If no coherent task exists, set acceptable=false and provide rejection_reason."""

SUBTASK_PLANNER_SYSTEM = """You are a stateful-task extraction auditor for AutomationBench.

The input contains an official source task, allowed assertion schemas, one
complete and untruncated successfully replayed random-walk trajectory, and deterministic state
differences. Extract one natural, coherent, executable business task from this evidence. A random
walk may contain unrelated queries and mutations: keep the full trajectory for evidence, but select
only the largest coherent set of state-changing steps that can be explicitly requested and scored
with the allowed assertion schemas. Never pretend unrelated mutations are one workflow.

Requirements:
1. Select at least one mutation. required_actions and write_contracts contain only selected mutation
   steps. A selected action must have a principal final-state result expressible by an allowed
   assertion schema and supported by a deterministic state difference.
2. Queries may support discovery for the selected task. Unrelated calls remain in step_coverage with
   a purpose beginning "excluded:" and must not enter the task plan.
3. Prefer a coherent subset matching the official source user prompt. Stable control values in
   selected mutation arguments may be promoted into explicit task requirements. Values first found
   in observations remain evidence the agent must discover, not user-known facts.
4. Extract exact field-level write contracts for every selected mutation, including destinations,
   relationships, dates, statuses, priorities, mappings, and free-text facts. Bound target_scope by
   names, IDs, or filters in the evidence; never broaden it to all records.
5. step_coverage must cover every trajectory step exactly once. Every natural-language string must
   be English.

Return the same JSON shape as the strict planner:
{
  "acceptable": true,
  "rejection_reason": "",
  "scenario": "one coherent business scenario",
  "user_known_inputs": ["inputs allowed in the request"],
  "target_scope": ["bounded target"],
  "required_actions": [{"step": 2, "endpoint_id": "service.operation", "intent": "action"}],
  "information_goals": [{"steps": [1], "purpose": "supporting discovery"}],
  "decision_rules": [{"condition": "condition", "result": "action", "source": "evidence"}],
  "write_contracts": [{"step": 2, "stable_fields": {}, "free_text_facts": {}}],
  "unresolved_ambiguities": [],
  "requested_output": {"format": "brief confirmation", "fields": []},
  "step_coverage": [{"step": 1, "purpose": "included purpose or excluded: reason"}]
}

If no scoreable coherent mutation exists, set acceptable=false and explain why."""

JOINT_SYSTEM = """You design English AutomationBench tasks and their executable assertions in one
shared semantic contract.

The input contains an official source task, an approved plan, the complete real trajectory with
observations and deterministic initial-to-final state differences. Use the
source user_prompt as an authoritative pre-execution business contract for rules that match this
trajectory and as a style reference. Use source assertions only as JSON-shape examples; never copy
them blindly. The trajectory and differences are feasibility evidence, not instructions to expose
incidental or discovered values in the task.

Generate the user question and all official AutomationBench assertions together. Requirements:

If extraction_mode is "coherent_subtask", selected_mutation_steps is the complete mutation scope of
the new task. Other trajectory mutations are random-walk noise: do not request them, assert them, or
declare the task unanswerable because they are unscored. You must still use the complete trajectory
as evidence for the selected steps and must never borrow outcomes from excluded steps.

QUESTION STYLE
1. Write one direct, business-oriented paragraph targeting 60–100 English words, with a hard
   maximum of 120 English words. Start with the
   workflow goal. Preserve applicable business rules and target inputs from the source user prompt,
   but do not copy discovered outcomes or unrelated source-task details. Prioritize executable scope,
   mappings, and field contracts over explanatory background, business rationale, or a requested
   completion summary when space is tight.
2. Do not use headings, bullets, numbered steps, placeholders, output templates, completion-report
   checklists, endpoint/API/tool/schema names, trajectory language, observations, or call order.
3. Explicitly request every planned state change and no unplanned action. Describe queries naturally
   as finding, checking, monitoring, filtering, or verifying business data.
4. Do not reveal generated IDs, timestamps, result counts, or post-execution discoveries as facts the
   user already knows. The question may ask the agent to discover those facts and act on them.
5. State every stable condition needed for scoring: target, destination, relationship, date, status,
   priority, category, and which discovered evidence must be recorded or communicated.
   Explicitly name or bound the target set using source-request inputs or query filters. Do not turn a
   path over two named companies into a request covering every Salesforce account.
6. Do not dictate complete prose for subjects, descriptions, summaries, or messages unless exact
   wording is itself a genuine business requirement.

CLARITY AND EXECUTABILITY
7. Make every conditional mapping explicit at the field level. If a signal class maps to a task
   priority, state each mapping, for example: "Use High task priority for funding and Medium task
   priority for growth." Never write only "set priority according to strength", "use the appropriate
   value", or another phrase that requires a hidden mapping.
8. For every asserted stable value, the question must either state the value or direct the agent to a
   specific authoritative record actually queried in the trajectory. The style-reference task is not
   an available external policy database: only its user_prompt is available as the current business
   contract. Do not mention any additional policy, procedure, or spreadsheet unless the current
   trajectory actually queries it.
   Stable control values and mappings taken directly from consistent mutation arguments may instead
   be stated as explicit rules of this newly designed question; do not pretend they came from an
   external policy.
9. Distinguish requirements from discoveries. A question may require the agent to discover a funding
   round, title change, or employee counts and record those facts. It must not present their observed
   values as user-known facts or turn those values into qualification rules. For example, if a query
   discovers Series B, Director to VP, or 25 percent growth, ask the agent to discover and record the
   funding round, title transition, or growth metrics; do not state those observed values in the
   question. Do not invent qualification thresholds that have no source.
   Do not infer a relationship stronger than the observation: current and previous titles establish a
   title change, not necessarily a hire, joining event, or promotion. Use a neutral category such as
   executive change unless the observation explicitly establishes the stronger relationship.
10. Give a field-specific free-text contract: say what the subject must identify, what the
    description/details must contain, and what each message must communicate. Specify business facts,
    not sentences or formatting. If different writes have different requirements, state the mapping
    clearly and concisely.
    Requirements are branch-specific: do not say every signal needs a date, person, amount, or other
    evidence when that fact exists only for some categories. For example, require a post date for a
    funding post and an effective date for a title change, but do not require a date for headcount data
    that has none. Never broaden a per-category reference field into an all-category requirement.
    Likewise, if one account update is mentioned in only one reference alert, state exactly which
    alert must report it; never use an unscoped phrase such as "and any priority update".
    Reference mutation prose may contain unsupported or over-specific wording. Do not inherit it:
    rewrite the requirement using only facts established by observations and score only those facts.

ASSERTION RULES
11. Return assertions in the exact registered AutomationBench JSON shapes in
   allowed_assertion_schemas. Use only listed types and allowed fields. Unsupported fields are
   silently ignored by some handlers and are strictly forbidden.
12. Every assertion must follow from an explicit requirement in the question and must be true in the
   supplied expected final WorldState. Cover every requested state change. Do not score query calls.
13. Stable structured fields may use equality. For free-form text, prefer the relevant *_contains,
   value_contains, text_contains, subject_contains, or description_contains field and require only a
   short atomic anchor: an entity, category, identifier, date, number, or noun phrase of at most 7
   words and 43 characters. Do not use a generated relational clause such as "company announced its
   funding", "person joined company", "headcount grew", or "task was created". Never assert full
   reference prose.
14. Make field placement match the question. For example, subject_contains is valid only when the
    question says what the subject should identify; description_contains is valid only when the
    question says the details/description should include that evidence.
    When a contains field accepts a list, group the short anchors that must occur in the same record
    or message instead of emitting unrelated existential checks.
    For slack_message_in_channel, text_contains must be a list of at least two atomic anchors so the
    company/signal and other principal requirement are verified in the same message. Use values such
    as ["SignalCorp", "Series B", "High", "follow-up task"], not formatted fragments such as
    "Funding Announcement (Strength: High)" or "Strength: High".
    Each alert list must include atomic anchors for the company, signal/category evidence, and every
    explicitly required stable label such as High or Medium. Do not omit a required strength anchor.
15. Never assert generated record IDs, generated timestamps, formatting, punctuation, or incidental
    defaults. Never copy a source assertion merely because it passes the final state.
16. Follow negative_assertion_requirement exactly. When required=true, include at least one assertion
    whose type is in allowed_types, and make the question explicitly state the matching narrow
    prohibition. Use it to guard a plausible false positive adjacent to the requested work, such as
    contacting an excluded recipient, posting in the wrong channel, creating an out-of-scope record,
    or changing an out-of-scope field. The guard must be true in the expected final WorldState. Do not
    invent an unrelated prohibition merely to satisfy this rule. When required=false, add a negative
    assertion only when the same conditions hold.

OUTPUT CONTRACT
17. assertion_rationales must align one-to-one with assertions. question_evidence must be an exact,
    contiguous quote from the generated question. value_origin must be explicit_requirement when the
    asserted value is specified by the request, discovered_evidence when the request requires that
    kind of fact to be discovered and recorded, or initial_state_guard for an unchanged-state guard.
18. Before returning, audit the question without using the hidden trajectory: a capable agent must be
    able to determine every required write, field value, conditional mapping, and output destination.
19. Every natural-language string must be English. If the task cannot be expressed and scored safely,
    return unanswerable=true with a reason.

Return JSON only:
{
  "unanswerable": false,
  "question": "one natural English user request",
  "assertions": [{"type": "registered_assertion_type", "other": "official fields"}],
  "assertion_rationales": [
    {
      "assertion_index": 0,
      "business_condition": "what is checked",
      "question_evidence": "an exact contiguous quote from the question",
      "value_origin": "explicit_requirement | discovered_evidence | initial_state_guard",
      "trajectory_evidence": "why the expected final state supports it"
    }
  ]
}"""

VERIFY_SYSTEM = """You are a strict acceptance auditor for jointly generated AutomationBench
questions and assertions.

Check the question, assertions, approved plan, complete reference trajectory, and deterministic state
differences as one semantic contract. Accept only if:
When extraction_mode is "coherent_subtask", selected_mutation_steps is the entire required mutation
scope. Ignore all other random-walk mutations when judging coverage; they remain visible only as
evidence and must not leak into the generated task.
1. every mutation step is explicitly requested and every mutation has at least one assertion for its
   principal business result;
2. every positive assertion is entailed by the question at the same field and semantic granularity;
3. every structured destination, relationship, status, date, priority, and conditional mapping is
   explicit in the question or points to an authoritative source actually queried by this trajectory;
   the target scope must also be explicit and bounded by source inputs or query filters;
4. no phrase such as "according to strength", "appropriate priority", "significant growth", or
   "follow the process" hides a value mapping, threshold, or decision rule required for correctness;
5. every free-text output has a field-specific fact contract, while its assertions use atomic entity,
   category, identifier, date, number, or noun-phrase anchors rather than generated relational clauses;
   current/previous title evidence must not be described as a hire, joining event, or promotion unless
   the observation explicitly establishes that relationship;
6. question_evidence in every rationale is an exact quote from the question and really entails the
   assertion; discovered values are not required to appear literally when the quoted requirement says
   to discover and record that kind of evidence;
   branch-specific requirements must not demand a fact absent from that branch's observation and
   write contract;
7. no result value is leaked as a fact the user already knows, although discovered evidence may be
   required in the resulting records/messages;
   specifically reject any condition that repeats a value first found in an observation as though it
   were a user-supplied threshold, category rule, or known event;
8. the question is one natural business paragraph targeting 60–100 English words, with a hard
   maximum of 120 English words, and has no headings,
   lists, placeholders, tool/API/schema language, endpoint narration, or report template;
9. all natural-language strings are English.

Apply official AutomationBench scoring granularity: score principal business outcomes and salient
semantic anchors, not every requested detail of generated prose. Several short anchors may be grouped
in a list to constrain one message, but independent existential contains checks are also normal in
official tasks. Do not demand record/message counts, uniqueness, or cross-assertion identity when the
allowed schemas cannot express them. A negative unchanged assertion may include the reference state's
existing expected_value even when the question naturally says only "leave it unchanged". Broad
anti-shotgun contains guards are normal in official tasks. Enforce negative_assertion_requirement:
when required=true, at least one assertion must use one of its allowed_types and the question must
explicitly state the corresponding prohibited action. Do not require a negative assertion type that
is absent from allowed_assertion_schemas.

Judge the generated question-and-assertion contract, not every incidental phrase in the reference
mutation bodies. A reference run may contain over-specific or unsupported prose such as "joined" or
"promoted" even though the observation proves only current title, previous title, and change date. If
the generated question neutralizes that wording and its assertions use only supported atomic facts,
do not reject it because of the reference prose. Reject only when the generated contract itself asks
for or scores the unsupported relationship.

covered_mutation_steps must exactly list all one-based mutation steps. covered_assertion_indices must
exactly list all zero-based assertion indexes. Return JSON only:
{
  "accepted": true,
  "reasons": [],
  "covered_mutation_steps": [2],
  "covered_assertion_indices": [0],
  "ambiguous_requirements": [],
  "leaked_result_values": []
}
Set accepted=false if any requirement is not met."""

TRAJECTORY_EVIDENCE_SYSTEM = """You extract exact task-design evidence from one chunk of a
successfully replayed AutomationBench tool trajectory. This chunk is one part of a larger trajectory;
do not judge the whole task and do not invent missing context.

For every supplied step:
1. Preserve its original one-based step number, endpoint_id, service, operation type, and whether it
   changed state.
2. For queries, record only concrete business facts and identifiers actually present in arguments or
   observations, including authoritative rules, filters, names, dates, categories, and values that a
   later action could depend on.
3. For mutations, record the exact target, stable written fields, destinations, relationships,
   statuses, priorities, dates, and short semantic facts placed in free text.
4. Distinguish explicit mutation controls from facts first discovered in query observations.
5. Do not omit a step. If a step has no useful business evidence, retain it with an empty facts list
   and explain that it is unrelated or administrative.
6. Keep strings and numbers verbatim. Every natural-language explanation must be English.

Return JSON only:
{
  "steps": [
    {
      "step": 1,
      "endpoint_id": "service.operation",
      "service": "service",
      "operation_type": "query | create | update | delete",
      "changed_state": false,
      "facts": ["concise exact fact"],
      "write_contract": {},
      "possible_dependencies": ["step-local evidence another action may consume"]
    }
  ]
}"""

STATE_DIFFERENCE_EVIDENCE_SYSTEM = """You extract exact scoreable evidence from one chunk of
deterministic initial-to-final WorldState differences produced by a successfully replayed
AutomationBench trajectory.

For every supplied difference:
1. Preserve its id, operation, path, expected count or value, and the shortest exact business anchors
   that identify the affected record or prohibited false positive.
2. Preserve stable destinations, relationships, statuses, priorities, dates, identifiers, and atomic
   free-text anchors verbatim.
3. State which official assertion types in the supplied allowed schemas could express the outcome.
4. Do not invent facts or omit a difference. Every natural-language explanation must be English.

Return JSON only:
{
  "differences": [
    {
      "id": "D1",
      "operation": "create | update | delete",
      "path": "$.service.collection",
      "business_outcome": "concise exact outcome",
      "stable_fields": {},
      "atomic_anchors": [],
      "candidate_assertion_types": []
    }
  ]
}"""

BATCH_PLANNER_SYSTEM = (
    PLANNER_SYSTEM.replace(
        "Decide whether the trajectory represents one natural, coherent, executable user goal and produce a\nstructured task plan.",
        "Design an executable batch of business operations and produce a structured task plan. Multiple independent objectives, accounts, services, and administrative operations are allowed in one request.",
    )
    .replace(
        "3. All steps must belong to one business scenario. Reject unrelated paths.",
        "3. Different business scenarios are allowed in the same batch. Do not reject a path merely because its objectives are independent.",
    )
    .replace("cannot\n   be expressed coherently", "cannot\n   be expressed executably")
    .replace("If no coherent task exists", "If no executable and scoreable task exists")
    .replace("one coherent business scenario", "a batch of multiple business objectives")
)

FULL_WORKFLOW_RULES = """

MULTI-OBJECTIVE EXECUTION CONTRACT (takes precedence over single-scenario style guidance)
Preserve tool-use complexity and all reference mutations. The new question may be a batch of
independent Sales operations, including operations for different accounts and different business
scenarios. Do NOT reject merely because there is no single natural business goal, narrative, causal
connection, or coherent workflow. Do NOT shrink the request to one easy email or update. Group
related information into natural continuous English prose that states the business goal, execution
requirements, and constraints. Write as a genuine business email, work ticket, or plain business
request. Do not use headings, bullet lists, numbered steps, checklists, or a task-by-task outline.
Independent objectives remain allowed; preserve them without inventing causal links between them.
Include necessary discoveries and dependencies across every reference service. If operations update
or overwrite the same record, state the required business order and final values unambiguously.
If an earlier state is superseded, assert the final durable result rather than demanding mutually
incompatible final states. Covers-all-mutations means that every mutation is requested; not that every
transient intermediate value must survive in the final WorldState. Assertions for final outcomes may
cover multiple sequential actions. Preserve executable prerequisites of selected writes.
The source prompt supplies applicable rules and known inputs; it does not restrict the new question
to its original single objective. Reference mutation arguments may supply explicit control values
for the new batch, including administrative operations. Do not invent unsupported external policies.
Keep essential multi-hop discovery, entity matching, decisions, writes, and verification. Redundant
read-only calls need not be imitated, but do not delete substantive work to make the question short.
Never add pointless calls or mention a tool-call quota in the question. The actual execution gate
will measure real tool usage independently.
Facts first obtained from a query must remain discoveries: name an authoritative source or bounded
lookup criterion rather than copying discovered contacts, titles, dates, amounts, or mappings into
the question. Stable user control rules may be stated directly. Do not expose generated IDs.
Use continuous English business paragraphs, up to 250 English words. Never reveal
API names or narrate endpoint calls. Multiple business objectives are expressly allowed.
Do not score generic prose concepts such as 'short call', 'migration plan', or 'like-for-like
comparison' with literal substring assertions unless the request explicitly requires those exact
words. Score stable structured outcomes and factual anchors. Every assertion must execute in the
original official registry and hold in the reference final state. Reject only concrete execution,
provenance, contradiction, or scoring problems; lack of single-scenario coherence is not a problem.

CONCISE QUESTION AND RESULT-FOCUSED SCORING (takes precedence over exhaustive field/prose coverage)
The question must be at most 250 English words. Keep every mutation and substantive discovery,
but summarize incidental narrative details rather than copying the entire write contract into the
question. Never remove operations or supply discovered answers merely to meet the word limit.
Use at most 12 native assertions, normally one combined assertion per affected record plus only
essential date, count, or negative checks. Group structured fields with native additional_checks.
Score each mutation's principal durable business outcome: correct entity, destination, relationship,
important status, date, amount, or classification. Select only essential factual anchors for prose;
incidental wording and every narrative detail need not receive separate assertions. Do not invent
unsupported compound handlers or silently ignored fields. All assertions must still be entailed by
the concise question and pass the unchanged official registry on the saved reference final state.
"""

GROUNDED_DIFFICULTY_RULES = """

GROUNDED DECISION DIFFICULTY (takes precedence over promoting mutation controls)
Keep the complete supplied trajectory, every mutation, its actual final state, and its original
business meaning unchanged. Do not add calls, new external facts, synthetic thresholds, invented
dependencies, or arbitrary puzzles. Multiple independent business objectives remain allowed: the
whole request need not form one coherent workflow. Difficulty must come from supported discovery
and decisions, not extra length, ambiguity, more assertions, or mandatory imitation of query calls.

Require at least two distinct evidence chains ending in requested, scored business outcomes.
Each chain must involve a substantive calculation, conditional decision, or cross-source
reconciliation, using facts in real query observations or independently audited initial-state query
evidence. At least one chain
must reconcile two distinct sources actually queried in the reference (for example policy and
transaction data, or separate authoritative records). Repeating a lookup, copying a supplied
literal, locating a known ID, and issuing an unconditional write are not substantive decisions.
If this unchanged trajectory cannot support the minimum, reject the candidate; never manufacture
difficulty or silently weaken the requirement.

The initial_state_query_evidence field is an offline audit of the SAME saved query arguments
against the actual initial checkpoint, with no mutations. It does not add or reorder reference
steps. A query recorded after a mutation may support the new task ONLY if this audit establishes
that the needed source already exists and can be read without changing the initial WorldState.
Use that initial observation, never a future-only record, a generated identifier, or a value created
by the reference writes. Do not reject merely because an independently verified initial source was
read later in the reference. Source observations before their dependent mutations remain valid.
All original mutations stay in scope. Preserve their principal durable business outcomes; unsupported
incidental reference prose is not an extra requirement. For independent administrative creation,
genuine new-record inputs may be supplied when they are not query-derived answers. Such copying
does not count as either of the two difficult chains. Never claim an unobserved ranking, comparison,
or completed selection merely because reference mutation prose asserts it.

Do not put query-derived classifications, per-record target statuses, computed amounts, totals,
rankings, winning entities, aggregate statistics, or completed report conclusions into the user
question. Require the agent to discover/compute them from specifically named, bounded sources.
A result copied into a later write argument remains a discovered result. Calling that result an
explicit override, stable status, user input, or administrative control does not make it known.
For example, ask the agent to apply the queried tolerance policy to the bounded invoice rows;
do not also list the resulting status for each row. Ask the agent to calculate performance and
identify leaders from the named records; do not supply the totals or winning article names.
Genuine user-supplied destinations, fixed execution dates, new-record inputs, and rule vocabulary
may stay explicit. A real pre-execution rule can be stated, but not the answer obtained by applying
it to each entity. Do not hide a needed rule: identify its accessible authoritative source or state
the genuine rule and leave the data-dependent decision to the agent.

When acting as the planner, add difficulty_contract to the normal planner JSON:
{
  "version": "grounded_decisions_v2",
  "chains": [{
    "id": "D1",
    "kind": "calculation | conditional_decision | cross_source_reconciliation",
    "query_steps": [1, 3],
    "mutation_steps": [8],
    "source_locators": ["specific record, workbook/tab/range, or bounded search"],
    "rule": "actual rule and its source, with no invented thresholds",
    "derived_values": [{"value": "hidden expected result", "source_steps": [1, 3],
                        "derivation": "how the observations establish this result"}]
  }]
}
Use real one-based step numbers. List at least two distinct chains. A cross_source_reconciliation
chain needs at least two distinct queried sources. These hidden expected results belong only in
the internal plan and assertions; they must remain discoveries in the user question.

When acting as the writer, preserve these chains under the existing 250-word cap, using natural English
continuous prose. Keep every mutation and independent objective. Prefer a business goal and
explicit evidence sources/decision rules to a recipe of already-decided field values. Do not fill
the 12-assertion allowance merely to reach the cap. Ensure at least two distinct, genuinely scored
assertions verify derived decision results, not just that an email/record exists or a supplied
literal was copied. Mark their rationales value_origin=discovered_evidence. Keep every assertion
supported by the official native handler, the question and the unchanged final WorldState.

When acting as the verifier, independently trace each claimed chain into the actual observations and the
generated question. Check that a changed source fact could change the required scored answer;
a chain that the question overrides with a fixed outcome fails this check. Reject answer leakage,
unbounded/vague sources, unscored reasoning decorations, invented dependencies, and any decision
that can be completed from the question alone without its claimed evidence. Do not accept the
planner's labels or value_origin labels as proof. In addition to the normal verifier JSON, return:
{
  "difficulty_audit": {
    "version": "grounded_decisions_v2",
    "no_answer_leakage": true,
    "sources_accessible_and_bounded": true,
    "reference_semantics_preserved": true,
    "leaked_answer_spans": [],
    "outcome_override_spans": [],
    "invented_dependencies": [],
    "validated_chains": [{
      "chain_id": "D1", "query_steps": [1, 3], "mutation_steps": [8],
      "assertion_indices": [0], "question_evidence": "exact contiguous question quote",
      "question_alone_insufficient": true,
      "counterfactual_test": "supported source change and resulting different scored outcome"
    }]
  }
}
Provide verified entries for at least two planned chains and at least one cross-source chain.
Assertions use zero-based indexes; each chain's decision must be measured by at least one assertion
that is not free in the initial state. The union must contain at least two distinct assertions.
Set accepted=false if any requirement fails. Do not infer difficulty just from a long trajectory.
"""
