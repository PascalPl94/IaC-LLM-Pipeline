"""Maintainer script to recreate version-2 synthetic inputs (not an experiment)."""

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/iac_experiments/assets"


def network(key, cidr, subnet, capacity=1, admin=False):
    return {
        "key": key,
        "name": f"exp-{key}",
        "cidr": cidr,
        "subnet": {
            "name": "admin-subnet" if admin else f"{key}-subnet",
            "cidr": subnet,
            "min_usable_addresses": capacity,
        },
    }


def scenario(id, count=1, pattern="A"):
    return {
        "schema_version": 1,
        "benchmark_version": 2,
        "scenario_id": id,
        "title": "Bounded Azure hub and spoke",
        "location": "westeurope",
        "resource_group": "rg-iac-experiment",
        "hub": network(
            "hub",
            "10.0.0.0/16",
            "10.0.1.0/24" if pattern == "B" else "10.0.0.0/24",
            admin=pattern == "B",
        ),
        "spokes": [
            network(f"spoke{i}", f"10.{i}.0.0/16", f"10.{i}.0.0/24") for i in range(1, count + 1)
        ],
        "security_pattern": pattern,
    }


def save(group, id, value):
    p = ROOT / group / f"{id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, indent=2) + "\n")


cases = [
    scenario("B01"),
    scenario("B02", 2),
    scenario("B03", pattern="B"),
    scenario("B04"),
    scenario("B05", 2),
    scenario("B06", 2),
    scenario("B07", 2, "B"),
    scenario("B08", pattern="B"),
]
cases[3]["spokes"][0]["subnet"].update(cidr="10.1.0.0/25", min_usable_addresses=60)
cases[4]["hub"] = network("hub", "10.64.0.0/16", "10.64.10.0/24")
cases[4]["spokes"] = [
    network("spoke1", "10.65.0.0/16", "10.65.20.0/24"),
    network("spoke2", "10.66.0.0/16", "10.66.30.0/24"),
]
cases[5]["spokes"][0]["subnet"].update(cidr="10.1.0.0/25", min_usable_addresses=60)
cases[5]["spokes"][1]["subnet"].update(cidr="10.2.0.0/26", min_usable_addresses=30)
cases[7]["hub"] = network("hub", "10.80.0.0/16", "10.80.5.0/24", admin=True)
cases[7]["spokes"] = [network("spoke1", "10.81.0.0/16", "10.81.10.0/26", 30)]
titles = [
    "One spoke HTTPS",
    "Two spokes HTTPS",
    "One spoke HTTPS and admin SSH",
    "One spoke capacity 60",
    "Alternative CIDRs two spokes",
    "Two spokes mixed capacities",
    "Two spokes admin SSH",
    "Alternative CIDRs capacity and admin SSH",
]
for s, title in zip(cases, titles, strict=True):
    s["title"] = title
    save("benchmark", s["scenario_id"], s)
for id, count, pattern in [("DEV01", 1, "A"), ("DEV02", 2, "B")]:
    s = scenario(id, count, pattern)
    s["hub"] = network("hub", "10.120.0.0/16", "10.120.5.0/24", admin=pattern == "B")
    s["spokes"] = [
        network(f"spoke{i}", f"10.{120 + i}.0.0/16", f"10.{120 + i}.7.0/24") for i in range(1, count + 1)
    ]
    save("dev", id, s)
invalid = []
for _ in range(6):
    invalid.append(copy.deepcopy(cases[0]))
del invalid[0]["location"]
invalid[1]["hub"]["cidr"] = "not-an-ip"
invalid[2]["spokes"][0]["cidr"] = "10.0.0.0/16"
invalid[3]["spokes"][0]["subnet"]["cidr"] = "10.9.0.0/24"
invalid[4]["spokes"][0]["subnet"].update(cidr="10.1.0.0/26", min_usable_addresses=60)
invalid[5]["language"] = "bicep"
rules = [
    "INPUT_SCHEMA",
    "INPUT_CIDR",
    "INPUT_OVERLAP",
    "INPUT_CONTAINMENT",
    "INPUT_CAPACITY",
    "INPUT_SCHEMA",
]
for i, (value, rule) in enumerate(zip(invalid, rules, strict=True), 1):
    save("rejection", f"IR{i:02}", {"test_id": f"IR{i:02}", "expected_rule": rule, "input": value})
