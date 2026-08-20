"""Prompt Registry domain models."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PromptStatus = Literal["draft", "testing", "published", "deprecated"]


class PromptTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: str
    name: str
    business_scene: str
    owner: str
    status: PromptStatus = "draft"
    current_version: int = 1
    published_version: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: datetime | None = None


class PromptVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: str
    version: int
    template: str
    variables: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    model: str
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    owner: str
    status: PromptStatus = "draft"
    change_log: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: datetime | None = None
    baseline_metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_contracts(self) -> "PromptVersion":
        validate_json_schema(self.input_schema, "input_schema")
        validate_json_schema(self.output_schema, "output_schema")
        if self.output_schema.get("type") == "object" and self.output_schema.get("additionalProperties") is not False:
            raise ValueError("output_schema must set additionalProperties=false")
        missing = [name for name in self.variables if "{" + name + "}" not in self.template]
        if missing:
            raise ValueError(f"Template is missing declared variables: {', '.join(missing)}")
        return self


class PromptDeployment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_id: str
    prompt_id: str
    version: int
    action: Literal["publish", "rollback"]
    previous_version: int | None = None
    environment: str = "production"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PromptEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str
    prompt_id: str
    version_a: int
    version_b: int
    test_case_count: int
    metrics_a: dict[str, float]
    metrics_b: dict[str, float]
    metric_delta: dict[str, float]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def validate_json_schema(schema: dict[str, Any], field_name: str = "schema") -> None:
    if not isinstance(schema, dict) or not schema:
        raise ValueError(f"{field_name} must be a non-empty JSON Schema object")
    allowed_types = {"object", "array", "string", "number", "integer", "boolean", "null"}

    def visit(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            raise ValueError(f"Invalid JSON Schema node at {path}")
        node_type = node.get("type")
        if node_type is not None and node_type not in allowed_types:
            raise ValueError(f"Invalid JSON Schema type at {path}: {node_type}")
        if node_type == "object":
            properties = node.get("properties", {})
            if not isinstance(properties, dict):
                raise ValueError(f"properties must be an object at {path}")
            required = node.get("required", [])
            if not isinstance(required, list) or any(item not in properties for item in required):
                raise ValueError(f"required must reference defined properties at {path}")
            for name, child in properties.items():
                visit(child, f"{path}.properties.{name}")
        if node_type == "array":
            if "items" not in node:
                raise ValueError(f"Array schema requires items at {path}")
            visit(node["items"], f"{path}.items")
        for keyword in ("anyOf", "oneOf", "allOf"):
            if keyword in node:
                if not isinstance(node[keyword], list):
                    raise ValueError(f"{keyword} must be an array at {path}")
                for index, child in enumerate(node[keyword]):
                    visit(child, f"{path}.{keyword}[{index}]")

    visit(schema, field_name)
