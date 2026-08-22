# ABOUTME: Regression tests for IDC identity resolution on API Gateway payload format 2.0
# ABOUTME: The quota API is an HTTP API, so IAM identity is under authorizer.iam, not identity.*

"""Tests for quota_check identity resolution via requestContext.authorizer.iam.

The deployed quota API is an API Gateway **HTTP API** with
``PayloadFormatVersion: 2.0``, which places the IAM caller under
``requestContext.authorizer.iam.*``. The Lambda previously read only
``requestContext.identity.*`` — the REST / payload 1.0 shape — so for IDC
deployments the caller ARN was always empty, identity never resolved, and
``MISSING_EMAIL_ENFORCEMENT`` (default ``"block"``) denied every request with
"Could not resolve user identity - access denied for security", regardless of
actual usage.

Covers both IDC username forms, which differ by identity source:
  - external IdP (e.g. Microsoft Entra ID) provisions the UPN, so the username
    is an email address
  - the built-in Identity Center directory allows bare usernames

Both must resolve. See also test_quota_idc_auth.py for the payload 1.0 shape,
which remains supported.
"""

import json
import os
import sys

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__), "..", "..", "deployment", "infrastructure", "lambda-functions", "quota_check"
    ),
)

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

import index


def _http_api_event(user_arn=None, user_id=None):
    """Build an API Gateway HTTP API (payload 2.0) event with IAM authorization."""
    iam = {}
    if user_arn:
        iam["userArn"] = user_arn
    if user_id:
        iam["userId"] = user_id
    return {"requestContext": {"authorizer": {"iam": iam}}}


def _reason(event):
    resp = index.lambda_handler(event, None)
    return json.loads(resp["body"]).get("reason")


class TestPayloadV2IdentityResolution:
    def test_entra_backed_idc_email_username_resolves(self):
        """External-IdP IDC: the username is a UPN, so the session name is an email."""
        event = _http_api_event(
            user_arn=(
                "arn:aws:sts::123456789012:assumed-role/"
                "AWSReservedSSO_BedrockDeveloperAccess_abc123/b.simon@example.onmicrosoft.com"
            )
        )
        assert _reason(event) != "missing_identity"

    def test_directory_backed_idc_bare_username_resolves(self):
        """Built-in Identity Center directory: bare username, no @."""
        event = _http_api_event(
            user_arn=(
                "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_BedrockDeveloperAccess_abc123/b.simon"
            )
        )
        assert _reason(event) != "missing_identity"

    def test_user_id_fallback_resolves_for_email_username(self):
        """When only userId is surfaced, the session name follows the colon."""
        event = _http_api_event(user_id="AROAEXAMPLEID:b.simon@example.onmicrosoft.com")
        assert _reason(event) != "missing_identity"

    def test_user_id_fallback_rejects_bare_username(self):
        """Without an ARN there is no way to confirm the caller is an SSO principal,
        so a bare session name stays rejected — an arbitrary assumed role must not be
        able to mint a quota identity from an opaque session string. Real IDC callers
        always surface userArn, so this only affects the defensive fallback.
        """
        assert _reason(_http_api_event(user_id="AROAEXAMPLEID:session123")) == "missing_identity"

    def test_no_iam_context_still_missing_identity(self):
        """An empty authorizer must not resolve to a bogus identity."""
        assert _reason({"requestContext": {"authorizer": {"iam": {}}}}) == "missing_identity"

    def test_user_id_without_colon_is_not_an_identity(self):
        """A malformed userId must not be accepted wholesale."""
        assert _reason(_http_api_event(user_id="AROAEXAMPLEID")) == "missing_identity"

    def test_jwt_still_wins_over_iam(self):
        """OIDC deployments must keep preferring the JWT email claim."""
        event = _http_api_event(
            user_arn="arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_Admin_x/iam-user@company.com"
        )
        event["requestContext"]["authorizer"]["jwt"] = {"claims": {"email": "jwt-user@company.com", "sub": "s"}}
        assert _reason(event) != "missing_identity"
