"""Atomic requirements derived from inputs, never from model-claimed mappings.

The same evaluator is used for both workflows. Shared predicates are an explicit
validity limitation; controlled mutation fixtures and a manual audit are required.
"""

from __future__ import annotations

import ipaddress
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .hcl import Graph, Ref, Resource, Unknown, display, unknown
from .models import RESOURCE_TYPES, Scenario


@dataclass
class Requirement:
    id: str
    category: str
    expected: Any
    observe: Callable[[Graph], tuple[Any, list[str]]]

    def score(self, graph: Graph) -> dict:
        if not graph.parsed:
            actual, evidence = Unknown("HCL could not be parsed completely"), []
        else:
            actual, evidence = self.observe(graph)
        return {
            "requirement_id": self.id,
            "category": self.category,
            "outcome": "unresolved"
            if unknown(actual)
            else ("pass" if actual == self.expected else "fail"),
            "expected": display(self.expected),
            "actual": display(actual),
            "evidence": evidence,
        }


def _pick(graph: Graph, kind: str, **attrs: Any) -> Resource | None | Unknown:
    matches = []
    unresolved = False
    for resource in graph.of_type(kind):
        observed = [graph.attr(resource, k) for k in attrs]
        if unknown(observed):
            unresolved = True
        elif all(graph.attr(resource, k) == v for k, v in attrs.items()):
            matches.append(resource)
    if len(matches) == 1:
        return matches[0]
    if unresolved:
        return Unknown(f"Cannot identify {kind} for {attrs}")
    return None


def patterns(scenario: Scenario, spoke_cidr: str) -> list[dict]:
    base = {"direction": "Inbound", "source_port_range": "*"}
    allow = {**base, "access": "Allow", "protocol": "Tcp", "destination_address_prefix": spoke_cidr}
    rules = [
        {
            **allow,
            "priority": 100,
            "source_address_prefix": scenario.hub.cidr,
            "destination_port_range": "443",
        }
    ]
    if scenario.security_pattern == "B":
        rules.append(
            {
                **allow,
                "priority": 110,
                "source_address_prefix": scenario.hub.subnet.cidr,
                "destination_port_range": "22",
            }
        )
    rules.append(
        {
            **base,
            "priority": 200,
            "access": "Deny",
            "protocol": "*",
            "source_address_prefix": "*",
            "destination_address_prefix": "*",
            "destination_port_range": "*",
        }
    )
    return rules


def requirements(scenario: Scenario) -> list[Requirement]:
    items: list[Requirement] = []

    def add(rid: str, category: str, expected: Any, observe: Callable) -> None:
        items.append(Requirement(rid, category, expected, observe))

    def attribute(rid: str, category: str, expected: Any, find: Callable, attr: str) -> None:
        def observe(graph: Graph) -> tuple[Any, list[str]]:
            resource = find(graph)
            if isinstance(resource, Unknown):
                return resource, []
            return graph.attr(resource, attr), (
                [f"{resource.file}:{resource.address}.{attr}"] if resource else []
            )

        add(rid, category, expected, observe)

    count = len(scenario.spokes)
    counts = dict.fromkeys(RESOURCE_TYPES, 0)
    counts.update(
        {
            "azurerm_virtual_network": count + 1,
            "azurerm_subnet": count + 1,
            "azurerm_virtual_network_peering": 2 * count,
            "azurerm_network_security_group": count,
            "azurerm_network_security_rule": count * (2 if scenario.security_pattern == "A" else 3),
            "azurerm_subnet_network_security_group_association": count,
        }
    )
    add(
        "SCOPE_RESOURCE_COUNTS",
        "scope",
        counts,
        lambda g: (
            dict(Counter(r.kind for r in g.resources.values()) | Counter({k: 0 for k in counts})),
            list(g.resources),
        ),
    )

    # Counter drops zero entries; all allowed types are required at least once.
    def nonoverlap(g: Graph) -> tuple[Any, list[str]]:
        networks = []
        resources = g.of_type("azurerm_virtual_network")
        for resource in resources:
            prefixes = g.attr(resource, "address_space")
            if unknown(prefixes):
                return Unknown("Unresolved actual VNet address space"), [resource.address]
            try:
                if not isinstance(prefixes, list) or len(prefixes) != 1:
                    return False, [resource.address]
                networks.append(ipaddress.IPv4Network(prefixes[0], strict=True))
            except (ValueError, TypeError):
                return False, [resource.address]
        valid = len(networks) == count + 1 and not any(
            a.overlaps(b) for i, a in enumerate(networks) for b in networks[i + 1 :]
        )
        return valid, [f"{r.file}:{r.address}.address_space" for r in resources]

    add("ADDRESSING_ACTUAL_VNET_NONOVERLAP", "addressing", True, nonoverlap)
    for node in [scenario.hub, *scenario.spokes]:

        def vnet_find(g, n=node):
            return _pick(g, "azurerm_virtual_network", name=n.name)

        def subnet_find(g, n=node):
            return _pick(g, "azurerm_subnet", name=n.subnet.name, virtual_network_name=n.name)

        for attr, expected in {
            "name": node.name,
            "location": scenario.location,
            "resource_group_name": scenario.resource_group,
            "address_space": [node.cidr],
        }.items():
            attribute(
                f"{node.key}.vnet.{attr}",
                "addressing" if attr == "address_space" else "scope",
                expected,
                vnet_find,
                attr,
            )
        for attr, expected in {
            "name": node.subnet.name,
            "resource_group_name": scenario.resource_group,
            "virtual_network_name": node.name,
            "address_prefixes": [node.subnet.cidr],
        }.items():
            attribute(
                f"{node.key}.subnet.{attr}",
                "addressing" if attr == "address_prefixes" else "connectivity",
                expected,
                subnet_find,
                attr,
            )

        def capacity(
            g: Graph, find: Callable = subnet_find, find_parent: Callable = vnet_find, n=node
        ) -> tuple[Any, list[str]]:
            resource = find(g)
            if isinstance(resource, Unknown):
                return resource, []
            parent_resource = find_parent(g)
            if isinstance(parent_resource, Unknown):
                return parent_resource, []
            prefixes = g.attr(resource, "address_prefixes")
            parent_prefixes = g.attr(parent_resource, "address_space")
            if unknown([prefixes, parent_prefixes]):
                return Unknown("Unresolved actual subnet or parent VNet prefix"), []
            try:
                subnet = ipaddress.IPv4Network(prefixes[0], strict=True)
                parent = ipaddress.IPv4Network(parent_prefixes[0], strict=True)
                valid = (
                    len(prefixes) == 1
                    and len(parent_prefixes) == 1
                    and subnet.subnet_of(parent)
                    and subnet.prefixlen <= 29
                    and subnet.num_addresses - 5 >= n.subnet.min_usable_addresses
                )
            except (ValueError, TypeError, IndexError):
                valid = False
            return valid, ([f"{resource.file}:{resource.address}.address_prefixes"] if resource else [])

        add(f"{node.key}.subnet.capacity_containment", "addressing", True, capacity)

    for spoke in scenario.spokes:
        for local, remote in [(scenario.hub, spoke), (spoke, scenario.hub)]:

            def peer_find(g: Graph, local_node=local, remote_node=remote) -> Resource | None | Unknown:
                remote_vnet = _pick(g, "azurerm_virtual_network", name=remote_node.name)
                if isinstance(remote_vnet, Unknown):
                    return remote_vnet
                return _pick(
                    g,
                    "azurerm_virtual_network_peering",
                    virtual_network_name=local_node.name,
                    remote_virtual_network_id=Ref(remote_vnet.address)
                    if remote_vnet
                    else Ref("missing"),
                )

            for attr, expected in {
                "resource_group_name": scenario.resource_group,
                "allow_virtual_network_access": True,
                "allow_forwarded_traffic": False,
                "allow_gateway_transit": False,
                "use_remote_gateways": False,
            }.items():
                attribute(
                    f"peering.{local.key}_to_{remote.key}.{attr}",
                    "connectivity",
                    expected,
                    peer_find,
                    attr,
                )

        def nsg_find(g: Graph, n=spoke) -> Resource | None | Unknown:
            subnet = _pick(g, "azurerm_subnet", name=n.subnet.name, virtual_network_name=n.name)
            if isinstance(subnet, Unknown):
                return subnet
            if not subnet:
                return None
            assoc = _pick(
                g, "azurerm_subnet_network_security_group_association", subnet_id=Ref(subnet.address)
            )
            if isinstance(assoc, Unknown):
                return assoc
            target = g.attr(assoc, "network_security_group_id")
            if isinstance(target, Unknown):
                return target
            if isinstance(target, Ref):
                resource = g.resources.get(target.address)
                if resource and resource.kind == "azurerm_network_security_group":
                    return resource
            return None

        for attr, expected in {
            "resource_group_name": scenario.resource_group,
            "location": scenario.location,
        }.items():
            attribute(f"{spoke.key}.associated_nsg.{attr}", "security", expected, nsg_find, attr)

        def rules_observe(g: Graph, find: Callable = nsg_find, n=spoke) -> tuple[Any, list[str]]:
            nsg = find(g)
            if isinstance(nsg, Unknown):
                return nsg, []
            if nsg is None:
                return [], []
            name = g.attr(nsg, "name")
            if unknown(name):
                return name, []
            wanted = patterns(scenario, n.subnet.cidr)
            fields = sorted(wanted[0])
            observed, evidence = [], []
            for rule in g.of_type("azurerm_network_security_rule"):
                owner = g.attr(rule, "network_security_group_name")
                if unknown(owner):
                    return owner, [rule.address]
                if owner == name:
                    values = {k: g.attr(rule, k) for k in fields}
                    # Azure protocol/direction/access enum strings are case-insensitive.
                    for enum_attr in ["direction", "access", "protocol"]:
                        if isinstance(values[enum_attr], str):
                            values[enum_attr] = values[enum_attr].lower()
                    values["resource_group_name"] = g.attr(rule, "resource_group_name")
                    observed.append(values)
                    evidence.append(f"{rule.file}:{rule.address}")
            if unknown(observed):
                return Unknown("Unresolvable NSG rule attributes"), evidence
            return sorted(observed, key=lambda x: repr(sorted(x.items()))), evidence

        expected_rules = [
            {**rule, "resource_group_name": scenario.resource_group}
            for rule in patterns(scenario, spoke.subnet.cidr)
        ]
        for rule in expected_rules:
            for attr in ["direction", "access", "protocol"]:
                rule[attr] = rule[attr].lower()
        add(
            f"{spoke.key}.exact_nsg_pattern_{scenario.security_pattern}",
            "security",
            sorted(expected_rules, key=lambda x: repr(sorted(x.items()))),
            rules_observe,
        )

    def distinct_associations(g: Graph) -> tuple[Any, list[str]]:
        associations = g.of_type("azurerm_subnet_network_security_group_association")
        ns = [g.attr(a, "network_security_group_id") for a in associations]
        if unknown(ns):
            return Unknown("Unresolved NSG association"), []
        return len(ns) == count and len(set(repr(x) for x in ns)) == count, [
            a.address for a in associations
        ]

    add("SECURITY_DISTINCT_SPOKE_NSGS", "security", True, distinct_associations)
    return items


def evaluate_intent(scenario: Scenario, graph: Graph) -> dict:
    scored = [requirement.score(graph) for requirement in requirements(scenario)]
    counts = Counter(row["outcome"] for row in scored)
    return {
        "requirements": scored,
        "passed": counts["pass"],
        "failed": counts["fail"],
        "unresolved": counts["unresolved"],
        "applicable": len(scored),
        "score": counts["pass"] / len(scored),
        "all_pass": counts["pass"] == len(scored),
    }
