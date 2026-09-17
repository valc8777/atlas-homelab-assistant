#!/usr/bin/env python3

"""Read-only Microsoft Graph request construction and response normalization."""

import json
import urllib.parse
import urllib.request


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


class GraphError(RuntimeError):
    pass


def _allowed_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "graph.microsoft.com" and parsed.path.startswith("/v1.0/")


def _get(url: str, access_token: str, opener=urllib.request.urlopen) -> dict:
    if not _allowed_url(url):
        raise GraphError("untrusted Microsoft Graph URL")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
    with opener(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise GraphError("invalid Microsoft Graph response")
    return payload


def paged_values(url: str, access_token: str, opener=urllib.request.urlopen, max_pages: int = 10) -> list[dict]:
    values = []
    for _ in range(max_pages):
        payload = _get(url, access_token, opener)
        page = payload.get("value", [])
        if not isinstance(page, list):
            raise GraphError("invalid Microsoft Graph collection")
        values.extend(item for item in page if isinstance(item, dict))
        url = payload.get("@odata.nextLink")
        if not url:
            return values
        if not _allowed_url(url):
            raise GraphError("untrusted Microsoft Graph pagination URL")
    raise GraphError("Microsoft Graph pagination limit exceeded")


def calendar_view_url(start_iso: str, end_iso: str) -> str:
    query = urllib.parse.urlencode({
        "startDateTime": start_iso,
        "endDateTime": end_iso,
        "$select": "id,subject,start,end,location,isAllDay,isCancelled,lastModifiedDateTime",
        "$top": "100",
    })
    return f"{GRAPH_ROOT}/me/calendarView?{query}"


def recent_mail_url(limit: int = 25, unread_only: bool = False) -> str:
    limit = max(1, min(int(limit), 50))
    params = {
        "$select": "id,subject,from,receivedDateTime,isRead,importance,webLink",
        "$orderby": "receivedDateTime desc",
        "$top": str(limit),
    }
    if unread_only:
        params["$filter"] = "isRead eq false"
    return f"{GRAPH_ROOT}/me/mailFolders/inbox/messages?{urllib.parse.urlencode(params)}"
