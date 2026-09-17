#!/usr/bin/env python3

"""Narrow Microsoft device-code helpers for the owner-only SFSU connector."""

import json
import urllib.error
import urllib.parse
import urllib.request


AUTHORITY = "https://login.microsoftonline.com/organizations/oauth2/v2.0"
DEFAULT_SCOPES = ("openid", "profile", "offline_access", "Calendars.ReadBasic", "Mail.ReadBasic")
ALLOWED_SCOPES = set(DEFAULT_SCOPES) | {"Mail.Read"}


class OAuthError(RuntimeError):
    pass


def validate_client_id(client_id: str) -> str:
    value = client_id.strip()
    if not value or len(value) > 128 or any(ch not in "0123456789abcdefABCDEF-" for ch in value):
        raise ValueError("invalid Microsoft application client ID")
    return value


def validate_scopes(scopes=DEFAULT_SCOPES) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(scopes))
    unexpected = set(values) - ALLOWED_SCOPES
    if unexpected:
        raise ValueError(f"unapproved Microsoft scopes: {sorted(unexpected)}")
    return values


def _post_form(url: str, values: dict, timeout: int = 20, opener=urllib.request.urlopen) -> dict:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(values).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as parse_exc:
            raise OAuthError(f"Microsoft returned HTTP {exc.code}") from parse_exc
    if not isinstance(payload, dict):
        raise OAuthError("invalid Microsoft response")
    return payload


def start_device_authorization(client_id: str, scopes=DEFAULT_SCOPES, opener=urllib.request.urlopen) -> dict:
    payload = _post_form(
        f"{AUTHORITY}/devicecode",
        {"client_id": validate_client_id(client_id), "scope": " ".join(validate_scopes(scopes))},
        opener=opener,
    )
    required = {"device_code", "user_code", "verification_uri", "expires_in", "interval"}
    if not required.issubset(payload):
        raise OAuthError("incomplete Microsoft device authorization response")
    return {key: payload[key] for key in required | {"message"} if key in payload}


def poll_device_token(client_id: str, device_code: str, scopes=DEFAULT_SCOPES, opener=urllib.request.urlopen) -> dict:
    if not device_code:
        raise ValueError("device code is required")
    return _post_form(
        f"{AUTHORITY}/token",
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": validate_client_id(client_id),
            "device_code": device_code,
            "scope": " ".join(validate_scopes(scopes)),
        },
        opener=opener,
    )


def exchange_authorization_code(client_id: str, code: str, redirect_uri: str, code_verifier: str, scopes=DEFAULT_SCOPES, opener=urllib.request.urlopen) -> dict:
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("redirect URI must be a localhost loopback address")
    if not code or not code_verifier:
        raise ValueError("authorization code and PKCE verifier are required")
    return _post_form(
        f"{AUTHORITY}/token",
        {
            "grant_type": "authorization_code",
            "client_id": validate_client_id(client_id),
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "scope": " ".join(validate_scopes(scopes)),
        },
        opener=opener,
    )
