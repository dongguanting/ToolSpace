import json

import pytest

from automation_environment.io import write_jsonl
from automation_environment.snapshots import load_snapshot, save_snapshot
from task_generation.llm import LLMClient
from tool_graph.graph import export_catalog
from tool_graph.inference import infer_edges, load_catalog, load_trajectories


def test_graph_does_not_order_parallel_calls(tmp_path):
    export_catalog(tmp_path / "catalog.json")
    _, methods, _ = load_catalog(tmp_path / "catalog.json")
    calls = [
        {
            "id": "first",
            "type": "function",
            "function": {
                "name": "api_fetch",
                "arguments": json.dumps(
                    {"method": "GET", "url": "https://slack.com/api/conversations.list"}
                ),
            },
        },
        {
            "id": "second",
            "type": "function",
            "function": {
                "name": "api_fetch",
                "arguments": json.dumps(
                    {
                        "method": "POST",
                        "url": "https://slack.com/api/chat.postMessage",
                        "body": {"channel": "CEXAMPLE01", "text": "Review ready"},
                    }
                ),
            },
        },
    ]
    payload = {
        "id": "parallel",
        "messages": [
            {"role": "assistant", "tool_calls": calls},
            {
                "role": "tool",
                "tool_call_id": "first",
                "content": json.dumps({"ok": True, "channels": [{"id": "CEXAMPLE01"}]}),
            },
            {"role": "tool", "tool_call_id": "second", "content": json.dumps({"ok": True})},
        ],
    }
    write_jsonl(tmp_path / "trajectories/test/correct.jsonl", [payload])
    batches, _, frequencies = load_trajectories(tmp_path / "trajectories", methods)
    assert infer_edges(batches, frequencies)[0] == {}
    payload["messages"][0]["tool_calls"] = [calls[0]]
    payload["messages"].insert(2, {"role": "assistant", "tool_calls": [calls[1]]})
    write_jsonl(tmp_path / "trajectories/test/correct.jsonl", [payload])
    batches, _, frequencies = load_trajectories(tmp_path / "trajectories", methods)
    assert len(infer_edges(batches, frequencies)[0]) == 1


def test_checkpoint_hash_detects_corruption(tmp_path):
    import gzip

    identifier = save_snapshot(tmp_path, {"meta": {}}, {"cursor": 1})
    assert load_snapshot(tmp_path, identifier)["runtime_state"]["cursor"] == 1
    with pytest.raises(ValueError):
        load_snapshot(tmp_path, "sha256:../../secret")
    path = next((tmp_path / "snapshots").glob("*/*.json.gz"))
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump({"world_state": {"meta": {}}, "runtime_state": {"cursor": 2}}, stream)
    with pytest.raises(ValueError, match="content hash mismatch"):
        load_snapshot(tmp_path, identifier)


@pytest.mark.parametrize("api", ["chat", "responses"])
def test_live_client_serializes_settings_and_strips_placeholder_key(tmp_path, api):
    import httpx

    recorded = []

    def handle(request):
        recorded.append(request)
        if api == "chat":
            result = {
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"accepted": true}'},
                    }
                ],
            }
        else:
            result = {
                "id": "test",
                "object": "response",
                "created_at": 0,
                "model": "test-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": '{"accepted": true}', "annotations": []}
                        ],
                    }
                ],
            }
        return httpx.Response(200, json=result)

    client = LLMClient(
        "https://model.example/v1", "", "test-model", api, "high", audit_dir=tmp_path
    )
    client.client._client._transport = httpx.MockTransport(handle)
    try:
        assert client.json("Generate JSON", {"evidence": [1, 2]}) == {"accepted": True}
    finally:
        client.close()
    body = json.loads(recorded[0].content)
    assert "authorization" not in recorded[0].headers
    assert body["model"] == "test-model"
    if api == "chat":
        assert body["reasoning_effort"] == "high"
        assert body["temperature"] == 0.7
    else:
        assert body["reasoning"] == {"effort": "high"}
    assert (tmp_path / "00001.json").is_file()
