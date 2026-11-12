"""Guards for the merge gate: the workflow and the branch ruleset must stay consistent."""

import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # ships with uvicorn[standard]

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RULESET = ROOT / ".github" / "rulesets" / "protect-main.json"
GATE_JOB = "ci-passed"


def jobs() -> dict[str, Any]:
    return dict(yaml.safe_load(WORKFLOW.read_text())["jobs"])


def test_ruleset_requires_the_gate_job() -> None:
    ruleset = json.loads(RULESET.read_text())
    (rule,) = [r for r in ruleset["rules"] if r["type"] == "required_status_checks"]
    contexts = [c["context"] for c in rule["parameters"]["required_status_checks"]]
    assert contexts == [jobs()[GATE_JOB]["name"]]


def test_gate_job_waits_for_every_other_job() -> None:
    all_jobs = jobs()
    gate = all_jobs[GATE_JOB]
    assert set(gate["needs"]) == set(all_jobs) - {GATE_JOB}
    # Without always() the gate is skipped when a job fails, and a skipped check can pass.
    assert gate["if"] == "always()"


def test_ruleset_cannot_be_bypassed() -> None:
    ruleset = json.loads(RULESET.read_text())
    assert ruleset["enforcement"] == "active"
    assert ruleset["bypass_actors"] == []
