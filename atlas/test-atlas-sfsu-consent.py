#!/usr/bin/env python3

"""One-time SFSU consent/read probe. Tokens remain in memory and are discarded."""

import argparse
import datetime
import time

import atlas_sfsu_graph as graph
import atlas_sfsu_oauth as oauth


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()

    authorization = oauth.start_device_authorization(args.client_id)
    print("Open:", authorization["verification_uri"], flush=True)
    print("Enter code:", authorization["user_code"], flush=True)
    if authorization.get("message"):
        print(authorization["message"], flush=True)

    deadline = time.monotonic() + min(args.timeout_seconds, int(authorization["expires_in"]))
    interval = max(5, int(authorization.get("interval", 5)))
    token = None
    while time.monotonic() < deadline:
        payload = oauth.poll_device_token(args.client_id, authorization["device_code"])
        error = payload.get("error")
        if not error:
            token = payload
            break
        if error == "authorization_pending":
            time.sleep(interval)
            continue
        if error == "slow_down":
            interval += 5
            time.sleep(interval)
            continue
        raise SystemExit(f"Consent failed: {error}: {payload.get('error_description', '')}")
    if not token:
        raise SystemExit("Consent timed out; no token was stored.")

    access_token = token.get("access_token")
    if not access_token:
        raise SystemExit("Microsoft response had no access token.")
    now = datetime.datetime.now(datetime.timezone.utc)
    events = graph.paged_values(
        graph.calendar_view_url(now.isoformat(), (now + datetime.timedelta(days=14)).isoformat()),
        access_token,
        max_pages=3,
    )
    messages = graph.paged_values(graph.recent_mail_url(limit=5), access_token, max_pages=1)
    granted = sorted(set((token.get("scope") or "").split()))
    print("Consent succeeded.")
    print("Granted scopes:", ", ".join(granted))
    print("Calendar read succeeded; event count in next 14 days:", len(events))
    print("Mail metadata read succeeded; recent header count:", len(messages))
    print("Access and refresh tokens were kept only in memory and are now discarded.")


if __name__ == "__main__":
    main()
