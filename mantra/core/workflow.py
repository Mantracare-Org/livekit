import logging
import json
import asyncio
from dataclasses import dataclass

from livekit.agents import AgentTask, llm
import httpx

logger = logging.getLogger("mantra.workflow")

@dataclass
class WorkflowResult:
    next_node_id: str | None = None
    extracted_data: dict | None = None

class LivekitWorkflowEngine:
    def __init__(self, workflow_json: dict, cc, initial_payload: dict):
        self.workflow_json = workflow_json
        self.cc = cc
        self.nodes = {n["id"]: n for n in workflow_json.get("nodes", [])}
        self.edges = workflow_json.get("edges", [])
        self.current_node_id = "start-1"  # Default starting node based on Dograh format
        self.initial_payload = initial_payload
        self.gathered_context = {}

    def get_outgoing_edges(self, node_id: str) -> list[dict]:
        return [e for e in self.edges if e.get("source") == node_id]

    async def execute_webhook(self, node: dict):
        data = node.get("data", {})
        url = data.get("endpoint_url")
        method = data.get("http_method", "POST")
        if not url:
            logger.warning("Webhook node has no endpoint_url")
            return

        payload = {}
        template = data.get("payload_template", {})
        for k, v in template.items():
            if isinstance(v, str) and v.startswith("{{") and v.endswith("}}"):
                var_path = v[2:-2].strip()
                if var_path.startswith("initial_context."):
                    var_name = var_path.split(".")[1]
                    payload[k] = self.initial_payload.get(var_name, "")
                elif var_path.startswith("gathered_context."):
                    var_name = var_path.split(".")[1]
                    payload[k] = self.gathered_context.get(var_name, "")
                else:
                    payload[k] = ""
            else:
                payload[k] = v

        logger.info(f"Executing webhook to {url} with payload: {payload}")
        try:
            async with httpx.AsyncClient() as client:
                if method.upper() == "POST":
                    await client.post(url, json=payload)
                else:
                    await client.get(url, params=payload)
        except Exception as e:
            logger.error(f"Failed to execute webhook: {e}")

class StartCallTask(AgentTask[WorkflowResult]):
    def __init__(self, node_data: dict, cc):
        super().__init__(
            instructions=node_data.get("prompt", "Hello!"),
            chat_ctx=None,
        )
        self.cc = cc
        self.node_data = node_data

    @llm.function_tool(
        description="Call this tool when you have greeted the user and the conversation has officially started."
    )
    async def call_started(self) -> None:
        """Move to the main agent stage."""
        logger.info("StartCallTask completed.")
        self.complete(WorkflowResult(next_node_id=None))

class AgentNodeTask(AgentTask[WorkflowResult]):
    def __init__(self, node_data: dict, cc):
        super().__init__(
            instructions=node_data.get("prompt", "You are an assistant."),
            chat_ctx=None,
        )
        self.cc = cc
        self.node_data = node_data
        
    @llm.function_tool(
        description="Call this tool when the conversation has reached its natural conclusion and you need to end the call."
    )
    async def end_call(self) -> None:
        """End the conversation and transition to extraction."""
        logger.info("AgentNodeTask completed.")
        self.complete(WorkflowResult(next_node_id=None))

class EndCallExtractionTask(AgentTask[WorkflowResult]):
    def __init__(self, node_data: dict, cc):
        
        instructions = "You are a silent data extraction agent. Do not speak. Extract the following from the conversation:\n"
        instructions += node_data.get("extraction_prompt", "")
        
        for var in node_data.get("extraction_variables", []):
            instructions += f"\n- {var['name']} ({var['type']}): {var['prompt']}"
            
        instructions += "\n\nCall the 'submit_extraction' tool immediately with your findings."
        
        super().__init__(
            instructions=instructions,
            chat_ctx=None,
        )
        self.cc = cc
        self.node_data = node_data

    @llm.function_tool(
        description="Submit the extracted data as a JSON string."
    )
    async def submit_extraction(self, data_json: str) -> None:
        """Submit the structured data."""
        logger.info(f"EndCallExtractionTask completed with data: {data_json}")
        try:
            data = json.loads(data_json)
        except Exception:
            data = {"raw": data_json}
        self.complete(WorkflowResult(next_node_id=None, extracted_data=data))

async def run_workflow(workflow_engine: LivekitWorkflowEngine):
    logger.info("Starting workflow execution")
    current_node_id = workflow_engine.current_node_id
    
    while current_node_id:
        node = workflow_engine.nodes.get(current_node_id)
        if not node:
            logger.error(f"Node {current_node_id} not found in workflow")
            break
            
        node_type = node.get("type")
        logger.info(f"Entering node: {current_node_id} of type {node_type}")
        
        if node_type == "startCall":
            task = StartCallTask(node.get("data", {}), workflow_engine.cc)
            await workflow_engine.cc.session.update_agent(task)
            await task
            
        elif node_type == "agentNode":
            task = AgentNodeTask(node.get("data", {}), workflow_engine.cc)
            await workflow_engine.cc.session.update_agent(task)
            await task
            
        elif node_type == "endCall":
            task = EndCallExtractionTask(node.get("data", {}), workflow_engine.cc)
            await workflow_engine.cc.session.update_agent(task)
            res = await task
            if res and res.extracted_data:
                workflow_engine.gathered_context.update(res.extracted_data)
            
        elif node_type == "webhook":
            await workflow_engine.execute_webhook(node)
            break
            
        else:
            logger.warning(f"Unknown node type: {node_type}")
            break
            
        edges = workflow_engine.get_outgoing_edges(current_node_id)
        if edges:
            current_node_id = edges[0].get("target")
        else:
            current_node_id = None
            
    logger.info("Workflow execution finished.")
