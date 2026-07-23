"""MCP 客户端：连接基于 stdio 的 MCP Server，并发现、转发工具调用。

为减少依赖，直接通过 stdio 使用 JSON-RPC，不额外引入 MCP SDK。配置读取自
`.claude/settings.json` 和 `~/.claude/settings.json`：

  { "mcpServers": { "name": { "command": "...", "args": [...], "env": {...} } } }

每个 MCP 工具都添加 `mcp__serverName__toolName` 前缀，防止不同 Server 的工具重名。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DiscoveredMcpTool:
    """MCP Server 暴露的远端工具描述。"""

    server_name: str
    remote_name: str
    description: str
    input_schema: dict


# ─── 单个 MCP 连接（每个 Server 一个）──────────────────────


class McpConnection:
    """管理单个 MCP Server 子进程及其 JSON-RPC 通信。"""

    def __init__(self, server_name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None):
        self.server_name = server_name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """启动 Server 子进程并开始异步读取响应。"""
        merged_env = {**os.environ, **self.env}
        self._process = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=merged_env,
        )
        # 后台持续读取 stdout，否则请求方等待响应时会形成读写死锁。
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        """从 stdout 读取按换行分隔的 JSON-RPC 响应，并唤醒对应请求。"""
        assert self._process and self._process.stdout
        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_id = msg.get("id")
            if msg_id is not None and msg_id in self._pending:
                fut = self._pending.pop(msg_id)
                if "error" in msg:
                    e = msg["error"]
                    fut.set_exception(
                        RuntimeError(f"MCP error {e.get('code')}: {e.get('message')}")
                    )
                else:
                    fut.set_result(msg.get("result"))

    async def _send_request(self, method: str, params: dict | None = None) -> Any:
        """发送 JSON-RPC 请求，并按请求 ID 等待对应响应。"""
        assert self._process and self._process.stdin
        req_id = self._next_id
        self._next_id += 1
        msg = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        self._process.stdin.write((msg + "\n").encode())
        await self._process.stdin.drain()
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        return await fut

    def _send_notification(self, method: str, params: dict | None = None) -> None:
        """发送无需响应的 JSON-RPC notification。"""
        if not self._process or not self._process.stdin:
            return
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}})
        self._process.stdin.write((msg + "\n").encode())

    async def initialize(self) -> None:
        """执行 MCP 初始化握手。"""
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "lion-code", "version": "1.0.0"},
        })
        self._send_notification("notifications/initialized")

    async def list_tools(self) -> list[DiscoveredMcpTool]:
        """发现当前 Server 提供的工具。"""
        result = await self._send_request("tools/list")
        if not result or not isinstance(result.get("tools"), list):
            return []
        return [
            DiscoveredMcpTool(
                server_name=self.server_name,
                remote_name=str(t["name"]),
                description=str(t.get("description", "")),
                input_schema=(
                    t["inputSchema"]
                    if isinstance(t.get("inputSchema"), dict)
                    else {"type": "object", "properties": {}}
                ),
            )
            for t in result["tools"]
        ]

    async def call_tool(self, name: str, args: dict) -> str:
        """调用工具，并合并响应中的文本内容。"""
        result = await self._send_request("tools/call", {"name": name, "arguments": args})
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            return "\n".join(
                c["text"] for c in result["content"] if c.get("type") == "text"
            )
        return json.dumps(result)

    def close(self) -> None:
        """终止 Server，并让所有未完成请求以异常结束。"""
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._process:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
            self._process = None
        # 必须显式结束 pending Future，避免上层永久等待已关闭的 Server。
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(f"MCP server '{self.server_name}' closed"))
        self._pending.clear()


# ─── MCP 连接管理器 ─────────────────────────────────────────


class McpManager:
    """管理全部 MCP Server 连接，并向 Agent 提供统一的工具定义和调用入口。"""

    def __init__(self):
        self._connections: dict[str, McpConnection] = {}
        self._tools: list[DiscoveredMcpTool] = []
        self._connected = False

    async def load_and_connect(self) -> None:
        """读取配置，连接全部 MCP Server 并发现工具；同一实例只初始化一次。"""
        if self._connected:
            return
        self._connected = True

        configs = self._load_configs()
        if not configs:
            return

        timeout = 15.0

        for name, cfg in configs.items():
            conn = McpConnection(
                name,
                cfg["command"],
                cfg.get("args"),
                cfg.get("env"),
            )
            try:
                await conn.connect()
                await asyncio.wait_for(conn.initialize(), timeout=timeout)
                server_tools = await asyncio.wait_for(conn.list_tools(), timeout=timeout)
                self._connections[name] = conn
                self._tools.extend(server_tools)
                print(f"[mcp] Connected to '{name}' — {len(server_tools)} tools", flush=True)
            except Exception as e:
                print(f"[mcp] Failed to connect to '{name}': {e}", flush=True)
                conn.close()

    async def discover_tools(self) -> list[DiscoveredMcpTool]:
        """连接尚未初始化时完成初始化，并返回已发现工具的稳定描述。"""
        await self.load_and_connect()
        return list(self._tools)

    async def call_remote_tool(
        self,
        *,
        server_name: str,
        tool_name: str,
        arguments: dict,
    ) -> str:
        """调用指定 Server 的远端工具，不解析公共名称。"""
        conn = self._connections.get(server_name)
        if not conn:
            raise RuntimeError(f"MCP server '{server_name}' not connected")
        return await conn.call_tool(tool_name, arguments)

    def get_tool_definitions(self) -> list[dict]:
        """Deprecated：返回旧版 Anthropic Schema，供迁移期调用方使用。"""
        return [
            {
                "name": f"mcp__{tool.server_name}__{tool.remote_name}",
                "description": tool.description
                or f"MCP tool {tool.remote_name} from {tool.server_name}",
                "input_schema": tool.input_schema,
            }
            for tool in self._tools
        ]

    async def call_tool(self, prefixed_name: str, args: dict) -> str:
        """Deprecated：解析旧版公共名称并转发到显式远端调用接口。"""
        parts = prefixed_name.split("__")
        if len(parts) < 3:
            raise ValueError(f"Invalid MCP tool name: {prefixed_name}")
        # 工具名本身可能包含 `__`，只能切出 Server 段后再拼回剩余部分。
        return await self.call_remote_tool(
            server_name=parts[1],
            tool_name="__".join(parts[2:]),
            arguments=args,
        )

    async def disconnect_all(self) -> None:
        """断开全部 Server 并清空已发现的工具。"""
        for conn in self._connections.values():
            conn.close()
        self._connections.clear()
        self._tools.clear()
        self._connected = False

    # ─── 配置加载 ────────────────────────────────────────────

    def _load_configs(self) -> dict[str, dict]:
        merged: dict[str, dict] = {}

        # 按“用户级 → 项目级 → .mcp.json”合并，后加载的同名 Server 覆盖前者。
        global_path = Path.home() / ".claude" / "settings.json"
        self._merge_config_file(global_path, merged)

        project_path = Path.cwd() / ".claude" / "settings.json"
        self._merge_config_file(project_path, merged)

        # `.mcp.json` 是 Claude Code 的项目级兼容配置入口，优先级最高。
        mcp_json_path = Path.cwd() / ".mcp.json"
        self._merge_config_file(mcp_json_path, merged)

        return merged

    def _merge_config_file(self, path: Path, target: dict[str, dict]) -> None:
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text())
            servers = raw.get("mcpServers", raw)
            for name, config in servers.items():
                if isinstance(config, dict) and "command" in config:
                    target[name] = config
        except Exception:
            pass  # 单个格式错误的配置不应阻止其他 MCP Server 启动。
