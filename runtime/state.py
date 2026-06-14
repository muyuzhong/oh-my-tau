"""SessionState：append-only 完整消息历史与 JSONL 转录。

转录文件本身就是最低成本的检查点：每条完整消息独占一行 JSON，进程恢复时
按顺序重放即可。上下文构建只能派生模型请求，不能修改这里保存的完整历史。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import List, Optional

from runtime.blocks import Message


class SessionState:
    """维护当前会话完整的内存消息历史和持久化转录。"""

    def __init__(self, session_id: Optional[str] = None, transcript_dir: str = "sessions"):
        self.session_id = session_id or f"sess_{uuid.uuid4().hex[:8]}"
        self.transcript_dir = Path(transcript_dir)
        self.messages: List[Message] = []

    @property
    def transcript_path(self) -> Path:
        return self.transcript_dir / f"{self.session_id}.jsonl"

    def append(self, message: Message) -> None:
        """先加入完整内存历史，再将同一条消息追加到转录末尾。"""
        self.messages.append(message)
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        with open(self.transcript_path, "a", encoding="utf-8") as transcript:
            transcript.write(json.dumps(message.to_dict(), ensure_ascii=False) + "\n")

    @classmethod
    def resume(cls, session_id: str, transcript_dir: str = "sessions") -> "SessionState":
        """从转录重建会话；恢复过程不再次写盘，避免复制历史。"""
        state = cls(session_id=session_id, transcript_dir=transcript_dir)
        with open(state.transcript_path, encoding="utf-8") as transcript:
            for line in transcript:
                if line.strip():
                    state.messages.append(Message.from_dict(json.loads(line)))
        return state
