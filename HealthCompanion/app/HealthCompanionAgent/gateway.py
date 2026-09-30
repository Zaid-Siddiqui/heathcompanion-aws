"""Tool access.

In the cloud the tools live behind AgentCore Gateway (MCP over HTTPS, AWS_IAM
auth). The runtime execution role signs every request with SigV4. For local
`agentcore dev` before the first deploy, when no gateway URL is present, the
same Lambda handler is imported and wrapped as in-process Strands tools so the
whole flow can be exercised end to end against the workshop data.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from mcp.client.streamable_http import streamablehttp_client
from strands import tool
from strands.tools.mcp.mcp_client import MCPClient

log = logging.getLogger(__name__)

GATEWAY_URL_ENV = "AGENTCORE_GATEWAY_HEALTH_TOOLS_URL"
REGION = os.environ.get("AWS_REGION", "us-west-2")


class SigV4HttpxAuth(httpx.Auth):
    """Sign httpx requests for the bedrock-agentcore service."""

    requires_request_body = True

    def __init__(self, service: str = "bedrock-agentcore", region: str = REGION):
        self._service = service
        self._region = region
        self._session = boto3.Session()

    def auth_flow(self, request: httpx.Request):
        creds = self._session.get_credentials()
        if creds is None:
            raise RuntimeError("No AWS credentials available to sign gateway requests")
        aws_req = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length", "connection")},
        )
        SigV4Auth(creds.get_frozen_credentials(), self._service, self._region).add_auth(aws_req)
        for k, v in aws_req.headers.items():
            request.headers[k] = v
        yield request


def gateway_mcp_client() -> MCPClient | None:
    url = os.environ.get(GATEWAY_URL_ENV)
    if not url:
        return None
    log.info("Using AgentCore Gateway tools at %s", url)
    auth = SigV4HttpxAuth()
    return MCPClient(lambda: streamablehttp_client(url, auth=auth))


def local_tools() -> list:
    """Wrap tools/health_tools/handler.py as in-process tools (dev only)."""
    tools_dir = Path(__file__).resolve().parents[2] / "tools" / "health_tools"
    if not tools_dir.exists():
        return []
    sys.path.insert(0, str(tools_dir))
    import handler as h  # noqa: WPS433 - deliberate late import

    log.warning("No %s set: using in-process tools from %s", GATEWAY_URL_ENV, tools_dir)

    @tool
    def triage_symptoms(symptoms: str, patient_id: str | None = None, language: str | None = None) -> dict:
        """Gauge urgency (emergency/urgent/routine/self-care) of symptoms against the triage guidelines, with citation."""
        return h.triage_symptoms(symptoms, patient_id, language)

    @tool
    def get_patient_history(patient_id: str) -> dict:
        """Fetch a patient's conditions, medications, allergies, insurance and preferred language."""
        return h.get_patient_history(patient_id)

    @tool
    def check_medications(patient_id: str, symptoms: str, additional_medications: str | None = None) -> dict:
        """Flag possible medication interactions or red flags for a pharmacist or doctor to confirm."""
        return h.check_medications(patient_id, symptoms, additional_medications)

    @tool
    def find_specialist(specialty: str, insurance: str | None = None, language: str | None = None,
                        location: str | None = None) -> dict:
        """Find providers by specialty, ordered by insurance coverage then earliest availability."""
        return h.find_specialist(specialty, insurance, language, location)

    @tool
    def book_appointment(patient_id: str, provider_id: str, preferred_date: str | None = None,
                         reason: str | None = None, language: str | None = None) -> dict:
        """Book an appointment with a provider and return the confirmation and a reminder of what to mention."""
        return h.book_appointment(patient_id, provider_id, preferred_date, reason, language)

    @tool
    def create_visit_summary(patient_id: str, symptoms: str, urgency: str, specialty: str | None = None,
                             interaction_flags: str | None = None, language: str | None = None) -> dict:
        """Write a bilingual doctor-ready visit summary to S3 and return its location, text and reminder."""
        return h.create_visit_summary(patient_id, symptoms, urgency, specialty, interaction_flags, language)

    return [triage_symptoms, get_patient_history, check_medications, find_specialist, book_appointment,
            create_visit_summary]


def load_tools() -> list:
    client = gateway_mcp_client()
    if client is not None:
        return [client]
    return local_tools()
