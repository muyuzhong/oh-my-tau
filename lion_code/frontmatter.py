"""供 Memory 与 Skill 共用的简化版 YAML frontmatter 解析器。

这里只支持 `---` 分隔符之间的 `key: value`，避免为受控配置格式引入完整 YAML 依赖。
"""

from dataclasses import dataclass, field


@dataclass
class FrontmatterResult:
    """解析后的元数据与正文；无法识别的 frontmatter 会原样归入正文。"""

    meta: dict[str, str] = field(default_factory=dict)
    body: str = ""


def parse_frontmatter(content: str) -> FrontmatterResult:
    """解析受支持的 frontmatter；格式不完整时保留原文，避免静默丢失内容。"""
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return FrontmatterResult(body=content)

    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        return FrontmatterResult(body=content)

    meta: dict[str, str] = {}
    for i in range(1, end_idx):
        colon_idx = lines[i].find(":")
        if colon_idx == -1:
            continue
        key = lines[i][:colon_idx].strip()
        value = lines[i][colon_idx + 1:].strip()
        if key:
            meta[key] = value

    body = "\n".join(lines[end_idx + 1:]).strip()
    return FrontmatterResult(meta=meta, body=body)


def format_frontmatter(meta: dict[str, str], body: str) -> str:
    """把简单键值元数据和正文序列化为项目约定的 Markdown 格式。"""
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)
