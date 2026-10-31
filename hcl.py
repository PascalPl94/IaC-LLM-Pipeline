"""Restricted HCL graph. No evaluation of functions, templates, variables or scripts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import hcl2

from .models import PROVIDER_VERSION, RESOURCE_TYPES, TERRAFORM_VERSION

FIELDS = {
    "azurerm_virtual_network": {"name", "location", "resource_group_name", "address_space", "tags"},
    "azurerm_subnet": {"name", "resource_group_name", "virtual_network_name", "address_prefixes"},
    "azurerm_virtual_network_peering": {
        "name",
        "resource_group_name",
        "virtual_network_name",
        "remote_virtual_network_id",
        "allow_virtual_network_access",
        "allow_forwarded_traffic",
        "allow_gateway_transit",
        "use_remote_gateways",
    },
    "azurerm_network_security_group": {"name", "location", "resource_group_name", "tags"},
    "azurerm_network_security_rule": {
        "name",
        "resource_group_name",
        "network_security_group_name",
        "priority",
        "direction",
        "access",
        "protocol",
        "source_port_range",
        "destination_port_range",
        "source_address_prefix",
        "destination_address_prefix",
        "description",
    },
    "azurerm_subnet_network_security_group_association": {"subnet_id", "network_security_group_id"},
}
DIRECT = re.compile(r"^\$\{(azurerm_[a-z_]+\.[A-Za-z_][A-Za-z0-9_-]*)\.(id|name)\}$")


@dataclass(frozen=True)
class Unknown:
    reason: str


@dataclass(frozen=True)
class Ref:
    address: str


@dataclass
class Resource:
    address: str
    kind: str
    attributes: dict
    file: str


@dataclass
class Graph:
    resources: dict[str, Resource] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    parsed: bool = True

    def add_issue(self, message: str, file: str, outcome: str = "fail") -> None:
        self.issues.append(
            {"rule_id": "OUTPUT_HCL_CONTRACT", "outcome": outcome, "file": file, "message": message}
        )

    def of_type(self, kind: str) -> list[Resource]:
        return [r for r in self.resources.values() if r.kind == kind]

    def resolve(self, value: Any, seen: frozenset[str] = frozenset()) -> Any:
        if isinstance(value, str):
            if match := DIRECT.fullmatch(value):
                address, attribute = match.groups()
                if address not in self.resources:
                    return Unknown(f"Reference to undeclared {address}")
                if attribute == "id":
                    return Ref(address)
                if address in seen:
                    return Unknown("Cyclic resource-name reference")
                return self.resolve(self.resources[address].attributes.get("name"), seen | {address})
            if "${" in value or "%{" in value:
                return Unknown(f"Unsupported expression: {value[:160]}")
            return value
        if isinstance(value, list):
            return [self.resolve(v, seen) for v in value]
        if isinstance(value, dict):
            return {k: self.resolve(v, seen) for k, v in value.items()}
        return value

    def attr(self, resource: Resource | None, name: str) -> Any:
        if resource is None:
            return None
        return self.resolve(resource.attributes.get(name))


def unknown(value: Any) -> bool:
    if isinstance(value, Unknown):
        return True
    if isinstance(value, (list, tuple)):
        return any(unknown(x) for x in value)
    if isinstance(value, dict):
        return any(unknown(x) for x in value.values())
    return False


def display(value: Any) -> Any:
    if isinstance(value, Ref):
        return {"resource_reference": value.address}
    if isinstance(value, Unknown):
        return {"unresolved": value.reason}
    if isinstance(value, (list, tuple)):
        return [display(x) for x in value]
    if isinstance(value, dict):
        return {k: display(v) for k, v in value.items()}
    return value


def parse_bundle(directory: Path) -> Graph:
    graph = Graph()
    documents = []
    for path in sorted(directory.glob("*.tf")):
        source = path.read_text(encoding="utf-8")
        if re.search(r"(?i)checkov\s*:\s*skip|bridgecrew\s*:\s*skip", source):
            graph.add_issue("Policy suppression comments are forbidden.", path.name)
        try:
            doc = _normalise_parser(hcl2.loads(source))
            _check_shape(doc)
        except Exception as exc:  # parser errors have several third-party exception types
            graph.parsed = False
            graph.add_issue(f"HCL parse error: {type(exc).__name__}: {str(exc)[:600]}", path.name)
            continue
        documents.append((path.name, doc))
        for top in doc:
            if top not in {"terraform", "provider", "resource", "output"}:
                graph.add_issue(f"Unsupported top-level block or attribute: {top}", path.name)
        for block in doc.get("resource", []):
            for kind, labels in block.items():
                for label, attrs in labels.items():
                    address = f"{kind}.{label}"
                    if address in graph.resources:
                        graph.add_issue(f"Duplicate resource {address}", path.name)
                        continue
                    graph.resources[address] = Resource(address, kind, attrs, path.name)
                    if kind not in RESOURCE_TYPES:
                        graph.add_issue(f"Excluded resource type: {kind}", path.name)
                    elif unsupported := set(attrs) - FIELDS[kind]:
                        graph.add_issue(
                            f"Unsupported attributes/blocks in {address}: {sorted(unsupported)}",
                            path.name,
                        )
    terraform = [block for _, doc in documents for block in doc.get("terraform", [])]
    providers = [block for _, doc in documents for block in doc.get("provider", [])]
    expected_tf = {
        "required_version": f"= {TERRAFORM_VERSION}",
        "required_providers": [
            {"azurerm": {"source": "hashicorp/azurerm", "version": f"= {PROVIDER_VERSION}"}}
        ],
    }
    # Whitespace and the optional '=' do not change an exact version constraint.
    # Normalise only this in-memory comparison, never the retained HCL.
    if len(terraform) == 1:
        spec = terraform[0]
        if _exact_pin(spec.get("required_version"), TERRAFORM_VERSION):
            spec["required_version"] = f"= {TERRAFORM_VERSION}"
        required = spec.get("required_providers")
        if isinstance(required, list) and len(required) == 1 and isinstance(required[0], dict):
            azurerm = required[0].get("azurerm")
            if isinstance(azurerm, dict):
                if _exact_pin(azurerm.get("version"), PROVIDER_VERSION):
                    azurerm["version"] = f"= {PROVIDER_VERSION}"
                if azurerm.get("source") == "registry.terraform.io/hashicorp/azurerm":
                    azurerm["source"] = "hashicorp/azurerm"
    if terraform != [expected_tf]:
        graph.add_issue(f"Exactly one terraform block is required with {expected_tf!r}.", "versions.tf")
    if providers != [{"azurerm": {"features": [{}]}}]:
        graph.add_issue("Exactly one azurerm provider with only features {} is required.", "provider.tf")
    for resource in graph.resources.values():
        if unknown(graph.resolve(resource.attributes)):
            graph.add_issue(
                f"Unsupported/unresolvable expression in {resource.address}", resource.file, "unresolved"
            )
    for file, doc in documents:
        for block in doc.get("output", []):
            for name, attrs in block.items():
                if set(attrs) - {"value", "description", "sensitive"} or unknown(graph.resolve(attrs)):
                    graph.add_issue(f"Unsupported output {name}", file, "unresolved")
    if not graph.resources:
        graph.add_issue("A non-empty resource configuration is required.", "main.tf")
    return graph


def _exact_pin(value: Any, version: str) -> bool:
    return (
        isinstance(value, str) and re.fullmatch(r"=?\s*" + re.escape(version), value.strip()) is not None
    )


def _check_shape(doc: Any) -> None:
    if not isinstance(doc, dict):
        raise ValueError("HCL document is not an object.")
    for top in {"terraform", "provider", "resource", "output"} & doc.keys():
        blocks = doc[top]
        if not isinstance(blocks, list) or not all(isinstance(b, dict) for b in blocks):
            raise ValueError(f"Malformed {top} block structure.")
        if top == "terraform":
            continue
        for block in blocks:
            for item in block.values():
                if not isinstance(item, dict):
                    raise ValueError(f"{top} must use block syntax, not an assigned value.")
                if top == "resource" and not all(isinstance(a, dict) for a in item.values()):
                    raise ValueError("Malformed resource label/attribute structure.")


def _normalise_parser(value: Any) -> Any:
    """bc-python-hcl2 wraps attribute values in one list and adds source metadata.

    Use only that pinned parser; installing python-hcl2 alongside Checkov would
    overwrite the same import namespace. Keep HCL collection values intact.
    """
    blocks = {"terraform", "provider", "resource", "output", "required_providers", "features"}
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"__start_line__", "__end_line__"}:
                continue
            if key not in blocks and isinstance(item, list) and len(item) == 1:
                item = item[0]
            result[key] = _normalise_parser(item)
        return result
    if isinstance(value, list):
        return [_normalise_parser(item) for item in value]
    return value
