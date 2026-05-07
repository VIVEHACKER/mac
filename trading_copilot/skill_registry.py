from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillRef:
    name: str
    path: Path


@dataclass(frozen=True)
class SkillBundle:
    workflow: str
    names: tuple[str, ...]
    text: str


class SkillRegistry:
    """Loads selected anthropics/financial-services markdown skills."""

    WORKFLOWS: dict[str, tuple[SkillRef, ...]] = {
        "morning": (
            SkillRef(
                "morning-note",
                Path("plugins/vertical-plugins/equity-research/skills/morning-note/SKILL.md"),
            ),
            SkillRef(
                "catalyst-calendar",
                Path("plugins/vertical-plugins/equity-research/skills/catalyst-calendar/SKILL.md"),
            ),
            SkillRef(
                "thesis-tracker",
                Path("plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md"),
            ),
        ),
        "pretrade": (
            SkillRef(
                "model-builder",
                Path("plugins/agent-plugins/model-builder/agents/model-builder.md"),
            ),
            SkillRef(
                "thesis-tracker",
                Path("plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md"),
            ),
            SkillRef(
                "portfolio-rebalance",
                Path("plugins/vertical-plugins/wealth-management/skills/portfolio-rebalance/SKILL.md"),
            ),
        ),
        "screen": (
            SkillRef(
                "idea-generation",
                Path("plugins/vertical-plugins/equity-research/skills/idea-generation/SKILL.md"),
            ),
            SkillRef(
                "market-researcher",
                Path("plugins/agent-plugins/market-researcher/agents/market-researcher.md"),
            ),
        ),
        "earnings": (
            SkillRef(
                "earnings-reviewer",
                Path("plugins/agent-plugins/earnings-reviewer/agents/earnings-reviewer.md"),
            ),
            SkillRef(
                "earnings-analysis",
                Path("plugins/vertical-plugins/equity-research/skills/earnings-analysis/SKILL.md"),
            ),
        ),
        "model": (
            SkillRef(
                "model-builder",
                Path("plugins/agent-plugins/model-builder/agents/model-builder.md"),
            ),
            SkillRef(
                "comps-analysis",
                Path("plugins/vertical-plugins/financial-analysis/skills/comps-analysis/SKILL.md"),
            ),
            SkillRef(
                "dcf-model",
                Path("plugins/vertical-plugins/financial-analysis/skills/dcf-model/SKILL.md"),
            ),
        ),
    }

    def __init__(self, financial_services_root: Path):
        self.root = financial_services_root

    def bundle_for(self, workflow: str) -> SkillBundle:
        key = workflow.lower().strip()
        refs = self.WORKFLOWS.get(key)
        if refs is None:
            available = ", ".join(sorted(self.WORKFLOWS))
            raise KeyError(f"Unknown workflow '{workflow}'. Available workflows: {available}")

        self._ensure_repo_exists()
        sections = []
        for ref in refs:
            source = self.root / ref.path
            if not source.exists():
                raise FileNotFoundError(
                    f"Missing financial-services skill file for {ref.name}: {source}"
                )
            sections.append(
                f"\n\n## Source Skill: {ref.name}\nPath: {ref.path.as_posix()}\n\n"
                + source.read_text(encoding="utf-8")
            )

        return SkillBundle(
            workflow=key,
            names=tuple(ref.name for ref in refs),
            text="".join(sections).strip(),
        )

    def _ensure_repo_exists(self) -> None:
        readme = self.root / "README.md"
        plugins = self.root / "plugins"
        if not readme.exists() or not plugins.exists():
            raise FileNotFoundError(
                "financial-services repository not found. Clone it next to trading-copilot "
                "or pass --financial-services-dir."
            )

