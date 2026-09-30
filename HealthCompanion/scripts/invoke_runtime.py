"""Invoke the deployed Health Companion runtime.

Usage:
  python scripts/invoke_runtime.py "prompt" [--patient PAT-01] [--session my-session] [--actor aisha]

Reads the runtime ARN from the CloudFormation stack outputs so nothing is hardcoded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

import boto3

REGION = os.environ.get("AWS_REGION", "us-west-2")
STACK = os.environ.get("AGENTCORE_STACK", "AgentCore-HealthCompanion-default")


def runtime_arn() -> str:
    cfn = boto3.client("cloudformation", region_name=REGION)
    outputs = cfn.describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]
    for o in outputs:
        if "RuntimeArn" in o["OutputKey"]:
            return o["OutputValue"]
    raise SystemExit(f"no RuntimeArn output on stack {STACK}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--patient", default=None)
    ap.add_argument("--language", default=None)
    ap.add_argument("--session", default=None, help="stable id for multi-turn / memory (min 33 chars; padded)")
    ap.add_argument("--actor", default=None)
    args = ap.parse_args()

    session = (args.session or uuid.uuid4().hex).ljust(33, "0")
    payload = {"prompt": args.prompt}
    if args.patient:
        payload["patient_id"] = args.patient
    if args.language:
        payload["language"] = args.language
    if args.actor:
        payload["actor_id"] = args.actor

    client = boto3.client("bedrock-agentcore", region_name=REGION)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn(),
        runtimeSessionId=session,
        payload=json.dumps(payload).encode("utf-8"),
        contentType="application/json",
        accept="text/event-stream",
    )
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    body = resp["response"]
    for raw in body.iter_lines():
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        delta = event.get("event", {}).get("contentBlockDelta", {}).get("delta", {})
        if "text" in delta:
            print(delta["text"], end="", flush=True)
    print()
    print(f"[session {session}]", file=sys.stderr)


if __name__ == "__main__":
    main()
