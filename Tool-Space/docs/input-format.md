# Workflow bundles

`automation-pro graph --input bundle.json --output outputs/graph` accepts a JSON object containing `tasks` and `workflows`. [examples/project_reconciliation.json](../examples/project_reconciliation.json) is a complete synthetic example.

```json
{
  "schema_version": "automation-pro-input-v1",
  "tasks": [{
    "example_id": 1,
    "prompt": [
      {"role": "system", "content": "Complete tasks using local API tools."},
      {"role": "user", "content": "Business request with its authoritative rules."}
    ],
    "info": {
      "task_name": "finance.example",
      "initial_state": {},
      "assertions": [],
      "zapier_tools": []
    }
  }],
  "workflows": [{
    "id": "example-workflow",
    "task_name": "finance.example",
    "calls": [{
      "tool": "api_fetch",
      "arguments": {"method": "GET", "url": "https://slack.com/api/conversations.list"}
    }]
  }]
}
```

Replace the empty fields with a valid WorldState, source assertion examples, and service/tool names. Initial entities and rules must make the supplied calls executable. Each workflow is run locally; graph relations are inferred from its actual observations rather than trusted supplied responses. Calls are sequential in this bundle format. The graph inference module also supports Atlas assistant batches and does not infer ordering between calls in the same batch.

The catalog is generated from the bundled schemas. A new service requires a schema model, route, API implementation and assertion handlers in the bundled environment. Merely adding an arbitrary endpoint to an input JSON file does not implement it.

`walk --config` accepts the fields of `tool_graph.walker.WalkConfig`, for example `min_steps`, `max_steps`, `min_distinct_services`, `min_mutations`, `min_queries_before_action`, `mutation_budget`, `query_only`, `template_scope`, and bounded search budgets. [examples/walk.json](../examples/walk.json) is suitable for the small demo. [examples/pro_walk.json](../examples/pro_walk.json) requires a broader source graph.

The pipeline can produce fewer walks than requested when constraints cannot be met. It exits 2 and preserves its search summary. Do not weaken verification to force a target count; provide richer valid source environments and workflows or change the intended task scope.
