"""Handwritten reference configurations for software verification ONLY.

This module is never imported by the real provider adapter or experiment runner.
Its outputs cannot be counted as LLM measurements.
"""

from __future__ import annotations

import json

from .intent import patterns
from .models import PROVIDER_VERSION, TERRAFORM_VERSION, Scenario


class Expression(str):
    pass


def block(kind: str, label: str, attributes: dict) -> str:
    lines = [f'resource "{kind}" "{label}" {{']
    for key, value in attributes.items():
        rendered = str(value) if isinstance(value, Expression) else json.dumps(value)
        lines.append(f"  {key} = {rendered}")
    return "\n".join([*lines, "}", ""])


def reference_output(s: Scenario) -> dict:
    blocks = []
    for node in [s.hub, *s.spokes]:
        blocks.append(
            block(
                "azurerm_virtual_network",
                node.key,
                {
                    "name": node.name,
                    "location": s.location,
                    "resource_group_name": s.resource_group,
                    "address_space": [node.cidr],
                },
            )
        )
        blocks.append(
            block(
                "azurerm_subnet",
                node.key,
                {
                    "name": node.subnet.name,
                    "resource_group_name": s.resource_group,
                    "virtual_network_name": Expression(f"azurerm_virtual_network.{node.key}.name"),
                    "address_prefixes": [node.subnet.cidr],
                },
            )
        )
    for spoke in s.spokes:
        for local, remote in [(s.hub, spoke), (spoke, s.hub)]:
            blocks.append(
                block(
                    "azurerm_virtual_network_peering",
                    f"{local.key}_to_{remote.key}",
                    {
                        "name": f"{local.key}-to-{remote.key}",
                        "resource_group_name": s.resource_group,
                        "virtual_network_name": Expression(f"azurerm_virtual_network.{local.key}.name"),
                        "remote_virtual_network_id": Expression(
                            f"azurerm_virtual_network.{remote.key}.id"
                        ),
                        "allow_virtual_network_access": True,
                        "allow_forwarded_traffic": False,
                        "allow_gateway_transit": False,
                        "use_remote_gateways": False,
                    },
                )
            )
        blocks.append(
            block(
                "azurerm_network_security_group",
                spoke.key,
                {
                    "name": f"{spoke.name}-nsg",
                    "resource_group_name": s.resource_group,
                    "location": s.location,
                },
            )
        )
        for rule in patterns(s, spoke.subnet.cidr):
            priority = rule["priority"]
            blocks.append(
                block(
                    "azurerm_network_security_rule",
                    f"{spoke.key}_p{priority}",
                    {
                        "name": f"rule-{priority}",
                        "resource_group_name": s.resource_group,
                        "network_security_group_name": Expression(
                            f"azurerm_network_security_group.{spoke.key}.name"
                        ),
                        **rule,
                    },
                )
            )
        blocks.append(
            block(
                "azurerm_subnet_network_security_group_association",
                spoke.key,
                {
                    "subnet_id": Expression(f"azurerm_subnet.{spoke.key}.id"),
                    "network_security_group_id": Expression(
                        f"azurerm_network_security_group.{spoke.key}.id"
                    ),
                },
            )
        )
    versions = f"""terraform {{
  required_version = "= {TERRAFORM_VERSION}"
  required_providers {{
    azurerm = {{
      source = "hashicorp/azurerm"
      version = "= {PROVIDER_VERSION}"
    }}
  }}
}}
"""
    return {
        "status": "ok",
        "network_plan": {"fixture_only": True},
        "terraform_files": [
            {"path": "versions.tf", "content": versions},
            {"path": "provider.tf", "content": 'provider "azurerm" {\n  features {}\n}\n'},
            {"path": "main.tf", "content": "\n".join(blocks)},
        ],
        "assumptions": ["Software verification fixture, not a model response."],
        "unsupported_requirements": [],
    }
