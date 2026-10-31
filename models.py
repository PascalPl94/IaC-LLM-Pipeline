"""Strict, versioned contracts. Domain rejection never changes a requested value."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]
ALLOWED_FILES = frozenset({"versions.tf", "provider.tf", "main.tf", "outputs.tf"})
REQUIRED_FILES = frozenset({"versions.tf", "provider.tf", "main.tf"})
PROVIDER_VERSION = "4.50.0"
TERRAFORM_VERSION = "1.9.8"
CHECK_IDS = ("CKV_AZURE_9", "CKV_AZURE_10")
RESOURCE_TYPES = (
    "azurerm_virtual_network",
    "azurerm_subnet",
    "azurerm_virtual_network_peering",
    "azurerm_network_security_group",
    "azurerm_network_security_rule",
    "azurerm_subnet_network_security_group_association",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Subnet(StrictModel):
    name: Name
    cidr: str
    min_usable_addresses: int = Field(ge=1, le=65531)


class Network(StrictModel):
    key: Name
    name: Name
    cidr: str
    subnet: Subnet


class Scenario(StrictModel):
    schema_version: Literal[1]
    benchmark_version: Literal[2]
    scenario_id: Annotated[str, Field(pattern=r"^(B0[1-8]|DEV0[12])$")]
    title: str = Field(min_length=1, max_length=250)
    location: Literal["westeurope", "northeurope", "uksouth"]
    resource_group: Name
    hub: Network
    spokes: list[Network] = Field(min_length=1, max_length=2)
    security_pattern: Literal["A", "B"]

    def domain_errors(self) -> list[dict]:
        errors: list[dict] = []
        parsed: list[tuple[str, ipaddress.IPv4Network]] = []
        nodes = [self.hub, *self.spokes]
        if len({n.key for n in nodes}) != len(nodes) or len({n.name for n in nodes}) != len(nodes):
            errors.append(
                {"rule_id": "INPUT_NAMES", "message": "Network keys and names must be unique."}
            )
        for node in nodes:
            try:
                parent = ipaddress.IPv4Network(node.cidr, strict=True)
                subnet = ipaddress.IPv4Network(node.subnet.cidr, strict=True)
            except ValueError as exc:
                errors.append({"rule_id": "INPUT_CIDR", "field": node.key, "message": str(exc)})
                continue
            parsed.append((node.key, parent))
            if not subnet.subnet_of(parent):
                errors.append(
                    {
                        "rule_id": "INPUT_CONTAINMENT",
                        "field": node.key,
                        "message": "Subnet outside parent VNet.",
                    }
                )
            if subnet.prefixlen > 29 or subnet.num_addresses - 5 < node.subnet.min_usable_addresses:
                errors.append(
                    {
                        "rule_id": "INPUT_CAPACITY",
                        "field": node.key,
                        "message": "Subnet must be /29 or larger with requested usable addresses after subtracting five.",
                    }
                )
        for i, (key, net) in enumerate(parsed):
            for other_key, other_net in parsed[i + 1 :]:
                if net.overlaps(other_net):
                    errors.append(
                        {
                            "rule_id": "INPUT_OVERLAP",
                            "field": [key, other_key],
                            "message": "VNet ranges overlap.",
                        }
                    )
        return errors

    def assert_supported(self) -> None:
        if errors := self.domain_errors():
            raise ValueError(str(errors))


class TerraformFile(StrictModel):
    path: str
    content: str = Field(min_length=1, max_length=600_000)

    @model_validator(mode="after")
    def safe(self) -> TerraformFile:
        if self.path not in ALLOWED_FILES:
            raise ValueError(f"Unsupported filename: {self.path!r}; only {sorted(ALLOWED_FILES)}.")
        if "\x00" in self.content:
            raise ValueError("NUL in file content.")
        return self


class GeneratedOutput(StrictModel):
    status: Literal["ok", "unsupported"]
    network_plan: dict
    terraform_files: list[TerraformFile] = Field(max_length=4)
    assumptions: list[str]
    unsupported_requirements: list[str]

    @model_validator(mode="after")
    def bundle(self) -> GeneratedOutput:
        names = [f.path for f in self.terraform_files]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate filenames.")
        if self.status == "ok":
            if not REQUIRED_FILES.issubset(names):
                raise ValueError(f"Required files: {sorted(REQUIRED_FILES)}.")
            if self.unsupported_requirements:
                raise ValueError("status=ok contradicts unsupported_requirements.")
        if sum(len(f.content.encode()) for f in self.terraform_files) > 1_000_000:
            raise ValueError("Terraform bundle exceeds 1 MB.")
        return self


class ExperimentConfig(StrictModel):
    schema_version: Literal[1] = 1
    provider: Literal["openai", "gemini"] = "openai"
    model: str = Field(min_length=1, max_length=100)
    temperature: float | None = Field(default=None, ge=0, le=2)
    generation_seed: int | None = None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    max_completion_tokens: int = Field(default=12000, ge=1024, le=64000)
    llm_timeout_seconds: float = Field(default=240.0, ge=1, le=1800)
    init_timeout_seconds: float = Field(default=180.0, ge=1, le=1800)
    tool_timeout_seconds: float = Field(default=120.0, ge=1, le=1800)
    max_repairs: Literal[2] = 2
    technical_retries: Literal[0, 1] = 1
    min_request_interval_seconds: float = Field(default=0.0, ge=0, le=3600)
    max_retry_wait_seconds: float = Field(default=120.0, ge=1, le=300)
    schedule_seed: int = 20261022
    terraform_bin: str = "terraform"
    checkov_bin: str = "checkov"
    terraform_version: Literal["1.9.8"] = TERRAFORM_VERSION
    azurerm_version: Literal["4.50.0"] = PROVIDER_VERSION
    checkov_version: Literal["3.3.30"] = "3.3.30"
    currency: Literal["USD"] = "USD"
    input_usd_per_million: float | None = Field(default=None, ge=0)
    output_usd_per_million: float | None = Field(default=None, ge=0)
    cached_input_usd_per_million: float | None = Field(default=None, ge=0)
    pricing_source: str | None = None
    pricing_date: str | None = None

    @model_validator(mode="after")
    def complete_price(self) -> ExperimentConfig:
        if self.model.startswith(("SET_", "YOUR_", "<")) or re.search(r"\s", self.model):
            raise ValueError("Set an actual model identifier supported by your account.")
        if self.provider == "gemini":
            if not re.fullmatch(r"gemini-[A-Za-z0-9][A-Za-z0-9._-]*", self.model):
                raise ValueError("Gemini provider requires a Gemini model ID, without models/ prefix.")
            if self.reasoning_effort is not None:
                if self.model.startswith(("gemini-3", "gemini-4")):
                    if self.reasoning_effort == "none":
                        raise ValueError("Thinking cannot be disabled for Gemini 3/4; use low.")
                    if (
                        self.model.startswith(("gemini-3.8", "gemini-4"))
                        and self.reasoning_effort == "minimal"
                    ):
                        raise ValueError(
                            "This Gemini family supports low/medium/high thinking; use low."
                        )
                elif self.model.startswith("gemini-2.5"):
                    if self.reasoning_effort == "none" and "pro" in self.model:
                        raise ValueError("Thinking cannot be disabled for Gemini 2.5 Pro.")
                else:
                    raise ValueError("Set reasoning_effort to null for this Gemini model family.")
        prices = [self.input_usd_per_million, self.output_usd_per_million]
        if any(p is not None for p in prices):
            if any(p is None for p in prices) or not self.pricing_source or not self.pricing_date:
                raise ValueError("Cost estimates require both prices, pricing_source and pricing_date.")
        return self
