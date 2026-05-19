"""Python AVM-boundary trace model for externally observable agent actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .evidence import EvidenceBundle, signed_evidence


@dataclass(frozen=True)
class ToolTrace:
    """Externally observable tool output captured by the AVM boundary."""

    tool_name: str
    output_type: str
    output: Dict[str, Any]
    timestamp: float
    source: str = "avm"
    dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "output_type": self.output_type,
            "output": self.output,
            "timestamp": self.timestamp,
            "source": self.source,
            "dependencies": list(self.dependencies),
        }


@dataclass(frozen=True)
class AgentAction:
    """Agent-visible action without private model chain-of-thought."""

    action_id: str
    action_type: str
    deployment_id: str
    timestamp: float
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "deployment_id": self.deployment_id,
            "timestamp": self.timestamp,
            "summary": self.summary,
        }


@dataclass
class DeploymentTrace:
    """Trace-to-evidence binding for one deployment request."""

    deployment_id: str
    actions: List[AgentAction] = field(default_factory=list)
    tools: List[ToolTrace] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "actions": [action.to_dict() for action in self.actions],
            "tools": [tool.to_dict() for tool in self.tools],
        }

    def to_evidence_bundle(self, secret_key: bytes) -> EvidenceBundle:
        evidence = []
        for index, tool in enumerate(self.tools):
            value = dict(tool.output)
            value.setdefault("deployment_id", self.deployment_id)
            evidence.append(
                signed_evidence(
                    evidence_id=f"{self.deployment_id}:{index}:{tool.output_type}",
                    evidence_type=tool.output_type,
                    value=value,
                    source=f"avm:{tool.tool_name}",
                    timestamp=tool.timestamp,
                    secret_key=secret_key,
                    dependencies=tool.dependencies,
                )
            )
        return EvidenceBundle(evidence)
