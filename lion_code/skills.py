"""Skill 系统：发现、解析并执行 `.claude/skills/*/SKILL.md`。

文件采用 frontmatter 元数据加提示词模板的结构，与 Claude Code 的 Skill 约定兼容。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .frontmatter import parse_frontmatter

# ─── 数据结构 ───────────────────────────────────────────────


@dataclass
class SkillDefinition:
    name: str
    description: str
    when_to_use: str | None = None
    allowed_tools: list[str] | None = None
    user_invocable: bool = True
    context: str = "inline"  # `inline` 复用主上下文，`fork` 使用隔离上下文。
    prompt_template: str = ""
    source: str = "project"  # `project` 的同名 Skill 优先于 `user`。
    skill_dir: str = ""


# ─── 发现与加载 ─────────────────────────────────────────────

_cached_skills: list[SkillDefinition] | None = None


def discover_skills() -> list[SkillDefinition]:
    global _cached_skills
    if _cached_skills is not None:
        return _cached_skills

    skills: dict[str, SkillDefinition] = {}

    # 先加载低优先级的用户级 Skill，便于项目级同名定义直接覆盖。
    user_dir = Path.home() / ".claude" / "skills"
    _load_skills_from_dir(user_dir, "user", skills)

    # 项目级 Skill 具有更高优先级。
    project_dir = Path.cwd() / ".claude" / "skills"
    _load_skills_from_dir(project_dir, "project", skills)

    _cached_skills = list(skills.values())
    return _cached_skills


def _load_skills_from_dir(
    base_dir: Path, source: str, skills: dict[str, SkillDefinition]
) -> None:
    if not base_dir.is_dir():
        return
    for entry in base_dir.iterdir():
        if not entry.is_dir():
            continue
        skill_file = entry / "SKILL.md"
        if not skill_file.exists():
            continue
        skill = _parse_skill_file(skill_file, source, str(entry))
        if skill:
            skills[skill.name] = skill


def _parse_skill_file(
    file_path: Path, source: str, skill_dir: str
) -> SkillDefinition | None:
    try:
        raw = file_path.read_text()
        result = parse_frontmatter(raw)
        meta = result.meta

        name = meta.get("name") or file_path.parent.name or "unknown"
        user_invocable = meta.get("user-invocable", "true") != "false"
        context = "fork" if meta.get("context") == "fork" else "inline"

        allowed_tools: list[str] | None = None
        if "allowed-tools" in meta:
            raw_tools = meta["allowed-tools"]
            if raw_tools.startswith("["):
                try:
                    allowed_tools = json.loads(raw_tools)
                except Exception:
                    allowed_tools = [s.strip() for s in raw_tools.strip("[]").split(",")]
            else:
                allowed_tools = [s.strip() for s in raw_tools.split(",")]

        return SkillDefinition(
            name=name,
            description=meta.get("description", ""),
            when_to_use=meta.get("when_to_use") or meta.get("when-to-use"),
            allowed_tools=allowed_tools,
            user_invocable=user_invocable,
            context=context,
            prompt_template=result.body,
            source=source,
            skill_dir=skill_dir,
        )
    except Exception:
        return None


# ─── 解析与执行 ─────────────────────────────────────────────


def get_skill_by_name(name: str) -> SkillDefinition | None:
    for s in discover_skills():
        if s.name == name:
            return s
    return None


def resolve_skill_prompt(skill: SkillDefinition, args: str) -> str:
    import re
    prompt = skill.prompt_template
    prompt = re.sub(r"\$ARGUMENTS|\$\{ARGUMENTS\}", args, prompt)
    prompt = prompt.replace("${CLAUDE_SKILL_DIR}", skill.skill_dir)
    return prompt


def execute_skill(
    skill_name: str, args: str
) -> dict | None:
    skill = get_skill_by_name(skill_name)
    if not skill:
        return None
    return {
        "prompt": resolve_skill_prompt(skill, args),
        "allowed_tools": skill.allowed_tools,
        "context": skill.context,
    }


# ─── 系统提示词片段 ─────────────────────────────────────────


def build_skill_descriptions() -> str:
    skills = discover_skills()
    if not skills:
        return ""

    lines = ["# Available Skills", ""]
    invocable = [s for s in skills if s.user_invocable]
    auto_only = [s for s in skills if not s.user_invocable]

    if invocable:
        lines.append("User-invocable skills (user types /<name> to invoke):")
        for s in invocable:
            lines.append(f"- **/{s.name}**: {s.description}")
            if s.when_to_use:
                lines.append(f"  When to use: {s.when_to_use}")
        lines.append("")

    if auto_only:
        lines.append("Auto-invocable skills (use the skill tool when appropriate):")
        for s in auto_only:
            lines.append(f"- **{s.name}**: {s.description}")
            if s.when_to_use:
                lines.append(f"  When to use: {s.when_to_use}")
        lines.append("")

    lines.append("To invoke a skill programmatically, use the `skill` tool with the skill name and optional arguments.")
    return "\n".join(lines)


def reset_skill_cache() -> None:
    global _cached_skills
    _cached_skills = None


def create_skill(name: str, content: str, scope: str = "project") -> str:
    """把模型提炼出的完整 `SKILL.md` 写入项目级或用户级目录。"""
    if not re.fullmatch(r"[a-z0-9-]+", name):
        return "Invalid skill name"

    root = Path.home() if scope == "user" else Path.cwd()
    skill_path = root / ".claude" / "skills" / name / "SKILL.md"
    if skill_path.exists():
        return "Skill already exists"

    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(content, encoding="utf-8")
    reset_skill_cache()
    return f"Skill created: {skill_path}"
