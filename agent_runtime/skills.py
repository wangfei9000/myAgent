"""SKILL.md 的发现和检索。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from .config import script_dir
from .util import parse_frontmatter, tokenize

@dataclass
class Skill:
    name: str
    description: str
    when_to_use: str
    body: str
    path: Path
    source: str

    def document(self) -> str:
        return " ".join(
            [
                self.name,
                self.name,
                self.name,
                self.description,
                self.description,
                self.when_to_use,
                self.body[:1500],
            ]
        )


class BM25:
    def __init__(self, docs: list[list[str]]):
        self.docs = docs
        self.n = len(docs)
        self.df: dict[str, int] = {}
        total = 0
        for doc in docs:
            total += len(doc)
            for token in set(doc):
                self.df[token] = self.df.get(token, 0) + 1
        self.avg = (total / self.n) if self.n else 0.0

    def score(self, query: list[str], index: int) -> float:
        doc = self.docs[index]
        if not doc or self.avg <= 0:
            return 0.0
        counts: dict[str, int] = {}
        for token in doc:
            counts[token] = counts.get(token, 0) + 1
        k1, b = 1.5, 0.75
        total = 0.0
        for token in query:
            freq = counts.get(token)
            if not freq:
                continue
            df = self.df.get(token, 0)
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            denom = freq + k1 * (1 - b + b * len(doc) / self.avg)
            total += idf * freq * (k1 + 1) / denom
        return total


def discover_skills(root: Path, user_dir: Path | None = None) -> list[Skill]:
    if user_dir is None:
        user_dir = Path.home() / "skills"
    found: dict[str, Skill] = {}
    skill_dirs = [script_dir() / "skills", root / "skills"]
    seen: set[Path] = set()
    ordered: list[tuple[str, Path]] = [("user", user_dir)]
    for directory in skill_dirs:
        resolved = directory.resolve() if directory.exists() else directory
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(("project", directory))
    for source, directory in ordered:
        if not directory.is_dir():
            continue
        for skill_file in sorted(directory.glob("*/SKILL.md")):
            try:
                meta, body = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
            except OSError:
                continue
            name = meta.get("name") or skill_file.parent.name
            found[name] = Skill(
                name=name,
                description=meta.get("description", ""),
                when_to_use=meta.get("when-to-use") or meta.get("when_to_use", ""),
                body=body,
                path=skill_file,
                source=source,
            )
    return list(found.values())


def retrieve_skills(skills: list[Skill], query: str, k: int = 3) -> list[tuple[Skill, float]]:
    if not skills:
        return []
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    index = BM25([tokenize(skill.document()) for skill in skills])
    ranked = [(skill, index.score(query_tokens, i)) for i, skill in enumerate(skills)]
    ranked = [item for item in ranked if item[1] > 0]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:k]


def format_skill_hits(hits: list[tuple[Skill, float]]) -> str:
    if not hits:
        return ""
    lines = ["[检索到的技能，需要完整步骤时调用 skill 工具]"]
    for skill, score in hits:
        when = f"；适用：{skill.when_to_use}" if skill.when_to_use else ""
        lines.append(f"- {skill.name} ({skill.source}, {score:.2f}): {skill.description}{when}")
    return "\n".join(lines)
