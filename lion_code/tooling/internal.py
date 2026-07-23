"""Agent、Skill、Plan、工具搜索与动态循环等内部工具定义。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .types import LionTool, ToolCapabilities, ToolResult


def create_agent_tool() -> LionTool:
    async def execute(context, tool_call_id, arguments, on_update):
        del tool_call_id, on_update
        return await context.controller.run_subagent_tool(arguments)

    return LionTool(
        name="agent",
        label="Agent",
        description="Launch a sub-agent to handle a task autonomously. Sub-agents have isolated context and return their result. Types: 'explore' (read-only), 'plan' (read-only, structured planning), 'general' (full tools).",
        parameters={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Short (3-5 word) description of the sub-agent's task",
                },
                "prompt": {
                    "type": "string",
                    "description": "Detailed task instructions for the sub-agent",
                },
                "type": {
                    "type": "string",
                    "enum": ["explore", "plan", "general"],
                    "description": "Agent type. Default: general",
                },
            },
            "required": ["description", "prompt"],
        },
        execute_fn=execute,
        capabilities=ToolCapabilities(
            allowed_in_plan=True,
            result_policy="persist_large",
        ),
    )


def create_skill_tool() -> LionTool:
    async def execute(context, tool_call_id, arguments, on_update):
        del tool_call_id, on_update
        return await context.controller.run_skill_tool(arguments)

    return LionTool(
        name="skill",
        label="Skill",
        description="Invoke a registered skill by name. Skills are prompt templates loaded from .claude/skills/. Returns the skill's resolved prompt to follow.",
        parameters={
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "The name of the skill to invoke",
                },
                "args": {
                    "type": "string",
                    "description": "Optional arguments to pass to the skill",
                },
            },
            "required": ["skill_name"],
        },
        execute_fn=execute,
        capabilities=ToolCapabilities(
            allowed_in_plan=True,
            result_policy="persist_large",
        ),
    )


def create_enter_plan_mode_tool() -> LionTool:
    async def execute(context, tool_call_id, arguments, on_update):
        del tool_call_id, arguments, on_update
        return await context.controller.enter_plan_mode_tool()

    return LionTool(
        name="enter_plan_mode",
        label="Enter plan mode",
        description="Enter plan mode to switch to a read-only planning phase. In plan mode, you can only read files and write to the plan file.",
        parameters={"type": "object", "properties": {}},
        execute_fn=execute,
        capabilities=ToolCapabilities(
            read_only=True,
            allowed_in_plan=True,
            deferred=True,
        ),
    )


def create_exit_plan_mode_tool() -> LionTool:
    async def execute(context, tool_call_id, arguments, on_update):
        del tool_call_id, arguments, on_update
        return await context.controller.exit_plan_mode_tool()

    return LionTool(
        name="exit_plan_mode",
        label="Exit plan mode",
        description="Exit plan mode after you have finished writing your plan to the plan file.",
        parameters={"type": "object", "properties": {}},
        execute_fn=execute,
        capabilities=ToolCapabilities(
            read_only=True,
            allowed_in_plan=True,
            deferred=True,
        ),
    )


def create_tool_search_tool() -> LionTool:
    async def execute(context, tool_call_id, arguments, on_update):
        del tool_call_id, on_update
        query = str(arguments.get("query", ""))
        matches = [
            tool
            for tool in context.registry.search(query)
            if tool.capabilities.deferred
        ]
        if not matches:
            return ToolResult(content="No matching deferred tools found.")

        activated: list[str] = []
        for tool in matches:
            context.registry.activate(tool.name)
            activated.append(tool.name)

        schemas = [tool.to_anthropic_schema() for tool in matches]
        return ToolResult(
            content=json.dumps(schemas, ensure_ascii=False, indent=2),
            activated_tools=activated,
        )

    return LionTool(
        name="tool_search",
        label="Tool search",
        description="Search for available tools by name or keyword. Returns full schema definitions for matching deferred tools so you can use them.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Tool name or search keywords",
                },
            },
            "required": ["query"],
        },
        execute_fn=execute,
        capabilities=ToolCapabilities(
            read_only=True,
            concurrency_safe=True,
            allowed_in_plan=True,
        ),
        execution_mode="parallel",
    )


def create_schedule_wakeup_tool() -> LionTool:
    async def execute(context, tool_call_id, arguments, on_update):
        del tool_call_id, on_update
        return await context.controller.schedule_wakeup_tool(arguments)

    return LionTool(
        name="schedule_wakeup",
        label="Schedule wakeup",
        description=(
            "Schedule when to resume work in /loop dynamic mode — you were invoked via /loop "
            "without an interval and are asked to self-pace. Pass the same /loop prompt back via "
            "`prompt` so the next firing repeats the task. To end the loop, simply do not call this "
            "tool. delaySeconds is clamped to [60, 3600]."
        ),
        parameters={
            "type": "object",
            "properties": {
                "delaySeconds": {
                    "type": "number",
                    "description": "Seconds from now to wake up (clamped to [60, 3600]).",
                },
                "reason": {
                    "type": "string",
                    "description": "One short sentence explaining the chosen delay.",
                },
                "prompt": {
                    "type": "string",
                    "description": "The /loop prompt to run on wake-up (pass the same prompt to repeat the task).",
                },
            },
            "required": ["delaySeconds", "reason", "prompt"],
        },
        execute_fn=execute,
        capabilities=ToolCapabilities(allowed_in_plan=True),
    )


def create_internal_tools() -> list[LionTool]:
    """创建常驻内部工具；schedule_wakeup 由动态循环临时注册。"""
    return [
        create_skill_tool(),
        create_enter_plan_mode_tool(),
        create_exit_plan_mode_tool(),
        create_agent_tool(),
        create_tool_search_tool(),
    ]


def create_legacy_mcp_tool(manager: Any, schema: Mapping[str, Any]) -> LionTool:
    """PR 2 兼容适配器；PR 4 会由带来源描述的正式 MCP Adapter 替换。"""
    public_name = str(schema["name"])

    async def execute(context, tool_call_id, arguments, on_update):
        del context, tool_call_id, on_update
        content = await manager.call_tool(public_name, dict(arguments))
        return ToolResult(content=content)

    return LionTool(
        name=public_name,
        label=public_name,
        description=str(schema.get("description", "")),
        parameters=dict(schema.get("input_schema", {})),
        execute_fn=execute,
        capabilities=ToolCapabilities(result_policy="persist_large"),
    )
