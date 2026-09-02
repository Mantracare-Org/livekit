# Chat Conversation

Note: _This is purely the output of the chat conversation and does not contain any raw data, codebase snippets, etc. used to generate the output._

### User Input

hwy can you please read and understand how the inbound call uses KB we might have to use the KB for outbound calls as well

*Grep searched codebase*

*Grep searched codebase*

*Viewed [agent.py](file:///home/fardeen/lkt/mantra/agent.py) *

*Viewed [Inbound Call Workflow.md](file:///home/fardeen/lkt/obsidian/Architecture/Inbound%20Call%20Workflow.md) *

*Viewed [Knowledge Base.md](file:///home/fardeen/lkt/obsidian/Features/Knowledge%20Base.md) *

### Planner Response

I have reviewed the codebase and the documentation (`obsidian/Features/Knowledge Base.md`, `obsidian/Architecture/Inbound Call Workflow.md`, and `mantra/agent.py`) to understand how the Knowledge Base (KB) is integrated into inbound calls. 

Here is a summary of how the inbound call flow utilizes the KB:

1. **Context Resolution (Pre-Call)**
   When an inbound SIP call hits the LiveKit server, the agent (`mantra/agent.py`) runs `resolve_inbound_context(phone_number)`. This looks up the dialed phone number in the PostgreSQL `org_configs` table to retrieve the specific `org_id` and associated `kb_ids` (from `kb_collections`) and `kb_tags`.

2. **Scoping the Assistant**
   An `AssistantFunctions` instance is created and initialized with the resolved `kb_ids` and `kb_tags`. This ensures the agent is strictly scoped to search only within that specific organization's documents.

3. **Pre-Loading / Zero-Latency Injection (Warmup)**
   During the agent's `warmup()` phase, it uses a `KnowledgeRetriever` to eagerly pre-fetch pages for the scoped `kb_ids`. It then injects up to 12,000 characters of this KB content directly into the agent's system prompt (using `format_upfront_kb_context`). This gives the LLM immediate context to answer questions with zero latency.

4. **On-Demand RAG Tool**
   For questions that aren't answered by the pre-loaded prompt, the LLM has access to a `search_knowledge_base` function tool. When invoked:
   - It searches the `kb_pages` PostgreSQL table using the `kb_ids` and `kb_tags` bounds.
   - It performs a **hybrid tiered search**:
     1. Strict Full-Text Search (FTS) + Semantic vector search (blended with Reciprocal Rank Fusion).
     2. Loose OR search if strict fails.
     3. Tag-only search.
     4. A fallback that lists available document titles so the LLM knows what *is* available instead of hallucinating.

### Next Steps for Outbound Calls
If we want to implement this for outbound calls, we would need to:
1. Ensure the outbound call payload (e.g., job metadata provided when initiating the outbound call) includes the `org_id` and any necessary `kb_ids`/`kb_tags`.
2. Extract those IDs in the agent entrypoint and pass them to `AssistantFunctions`.
3. The rest of the pipeline (`warmup` injection and the `search_knowledge_base` tool) should automatically work since they rely on the properties initialized on `AssistantFunctions`.

Would you like me to start investigating how outbound calls are currently initiated (e.g. in your `livekit-mcp` project or elsewhere) so we can plan how to pass this KB metadata?

### User Input

I was wondering if we could use the KB for oubound call as well, like if the human asks any question and the agent doesn't really know the answer for it it can refer the KB and answer it 

*Viewed [agent.py](file:///home/fardeen/lkt/mantra/agent.py) *

*Grep searched codebase*

*Grep searched codebase*

*Grep searched codebase*

*Viewed [lkt_client.py](file:///home/fardeen/livekit-mcp/src/livekit_mcp/clients/lkt_client.py) *

*Grep searched codebase*

*Listed directory [livekit_mcp](file:///home/fardeen/livekit-mcp/src/livekit_mcp) *

*Listed directory [tools](file:///home/fardeen/livekit-mcp/src/livekit_mcp/tools) *

*Grep searched codebase*

*Grep searched codebase*

### Planner Response

Yes, absolutely! In fact, **the agent codebase is already set up to support this seamlessly.**

If you look at how the agent parses the call context (`mantra/agent.py` lines 816–826), you can see that the KB extraction logic runs for **all** calls, regardless of whether they are inbound or outbound:

```python
if meta_payload.get("org_id"):
    call_state["org_id"] = meta_payload.get("org_id")
    kb_ids_list.append(str(meta_payload["org_id"]))
if "kb_id" in meta_payload and meta_payload["kb_id"]:
    kb_ids_list.append(str(meta_payload["kb_id"]))
if "kb_ids" in meta_payload and isinstance(meta_payload["kb_ids"], list):
    kb_ids_list.extend([str(k) for k in meta_payload["kb_ids"]])
```

### How to use it for Outbound Calls:
Since outbound calls already know who they are dialing and on behalf of which organization, you skip the database lookup step used by inbound calls. All you have to do is **include the `org_id` (and optionally `kb_tags`) in the JSON payload** when you trigger the outbound call.

For example, when your backend or MCP tool calls the outbound webhook (e.g., `/api/v1/webhooks/telephony`), your payload would just look like this:

```json
{
  "direction": "outbound",
  "phone_number": "+1234567890",
  "org_id": "org_12345",
  "kb_tags": ["sales", "pricing"] 
}
```

Once the LiveKit job starts:
1. The agent will read `org_id` from the metadata.
2. It will instantiate `AssistantFunctions` with those `kb_ids`.
3. It will run `warmup()` to pre-fetch the KB into the system prompt for zero-latency answers.
4. The LLM will have access to the `search_knowledge_base` tool to look up any factual questions the human asks that aren't already in the prompt.

You don't need to write any new logic in the agent! Just pass the `org_id` in the outbound trigger payload and the agent will handle the rest identically to an inbound call.