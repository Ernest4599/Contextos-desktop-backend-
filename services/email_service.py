"""
Email sending via AWS SES. Used for account verification and password
reset links - the only two email flows in the app so far.

Requires these environment variables:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION - SES credentials
  SES_FROM_EMAIL - the verified sending identity
  CONTEXTOS_DEV - base URL of the frontend, used to build links in emails.
    Update this when the frontend's reachable address changes (local
    network IP, eventual hosted URL, eventual real domain) - nothing
    else in this file needs to change when that happens.
"""
from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError

AWS_REGION = os.environ.get("AWS_REGION", "")
SES_FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "")
FRONTEND_BASE_URL = os.environ.get("CONTEXTOS_DEV", "")


class EmailError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _get_ses_client():
    if not AWS_REGION:
        raise EmailError("Email sending isn't configured right now.")
    return boto3.client("ses", region_name=AWS_REGION)


def send_email(to: str, subject: str, html_body: str) -> None:
    if not SES_FROM_EMAIL:
        raise EmailError("Email sending isn't configured right now.")

    client = _get_ses_client()
    try:
        client.send_email(
            Source=SES_FROM_EMAIL,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
            },
        )
    except ClientError as e:
        print(f"[EMAIL] SES send failed for {to}: {e}")
        raise EmailError("Couldn't send email right now. Please try again.")


def send_verification_email(to: str, token: str) -> None:
    link = f"{FRONTEND_BASE_URL}/verify-email?token={token}"
    html_body = f"""
    <p>Welcome to ContextOS.</p>
    <p>Please confirm your email address to activate your account:</p>
    <p><a href="{link}">Verify your email</a></p>
    <p>This link expires in 24 hours. If you didn't create this account, you can ignore this email.</p>
    """
    send_email(to, "Verify your ContextOS account", html_body)


def send_password_reset_email(to: str, token: str) -> None:
    link = f"{FRONTEND_BASE_URL}/reset-password?token={token}"
    html_body = f"""
    <p>We received a request to reset your ContextOS password.</p>
    <p><a href="{link}">Reset your password</a></p>
    <p>This link expires in 1 hour. If you didn't request this, you can ignore this email - your password won't be changed.</p>
    """
    send_email(to, "Reset your ContextOS password", html_body)
