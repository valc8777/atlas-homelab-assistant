#!/usr/bin/env python3

"""Read-only, allowlisted Nextcloud DAV adapters for ATLAS."""

from __future__ import annotations

import base64
import datetime as dt
import ipaddress
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


MAX_DAV_BYTES = 2_000_000
MAX_DAV_RECORDS = 500
DAV = "DAV:"
ICAL = "urn:ietf:params:xml:ns:caldav"
CARDDAV = "urn:ietf:params:xml:ns:carddav"
ICAL_DATA = f"{{{ICAL}}}calendar-data"
VCARD_DATA = f"{{{CARDDAV}}}address-data"


def validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value.rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Nextcloud URL must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Nextcloud URL must not contain credentials, query, or fragment")
    host = parsed.hostname.lower()
    private_host = host.endswith(".ts.net")
    try:
        private_host = private_host or ipaddress.ip_address(host).is_private
    except ValueError:
        pass
    if not private_host:
        raise ValueError("Nextcloud must use a private-network endpoint")
    return value.rstrip("/")


def validate_path(path: str, allowlist: list[str]) -> str:
    normalized = "/".join(part for part in path.strip("/").split("/") if part not in {"", "."})
    if ".." in path.split("/"):
        raise PermissionError("Nextcloud path traversal is not allowed")
    if not normalized or not any(normalized == root or normalized.startswith(root.rstrip("/") + "/") for root in allowlist):
        raise PermissionError("Nextcloud path is not allowlisted")
    return normalized


def validate_name(name: str, allowlist: list[str], kind: str) -> str:
    normalized = " ".join(name.strip().split())
    if not normalized:
        raise ValueError(f"Nextcloud {kind} name is required")
    if "/" in normalized or "\\" in normalized or ".." in normalized:
        raise PermissionError(f"Nextcloud {kind} name is invalid")
    if normalized not in allowlist:
        raise PermissionError(f"Nextcloud {kind} is not allowlisted")
    return normalized


def _credential(username: str, app_password: str) -> str:
    if not username or not app_password:
        raise ValueError("Nextcloud reader credential is not configured")
    return base64.b64encode(f"{username}:{app_password}".encode("utf-8")).decode("ascii")


def _propfind_request(url: str, credential: str, body: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        method="PROPFIND",
        headers={
            "Authorization": f"Basic {credential}",
            "Depth": "1",
            "Content-Type": "application/xml; charset=utf-8",
        },
        data=body.encode("utf-8"),
    )


def _report_request(url: str, credential: str, body: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        method="REPORT",
        headers={
            "Authorization": f"Basic {credential}",
            "Depth": "1",
            "Content-Type": "application/xml; charset=utf-8",
        },
        data=body.encode("utf-8"),
    )


def _href_url(base_url: str, href: str, kind: str) -> str:
    parsed_href = urllib.parse.urlparse(href).path
    if not parsed_href.startswith("/"):
        raise ValueError(f"Nextcloud {kind} href is invalid")
    parsed_base = urllib.parse.urlparse(validate_base_url(base_url))
    return urllib.parse.urlunparse((parsed_base.scheme, parsed_base.netloc, parsed_href, "", "", ""))


def _parse_xml(data: bytes, kind: str) -> ET.Element:
    if len(data) > MAX_DAV_BYTES:
        raise ValueError(f"Nextcloud {kind} response exceeds size limit")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"Nextcloud {kind} response is invalid XML") from exc


def parse_multistatus(data: bytes) -> list[dict]:
    root = _parse_xml(data, "DAV")
    records = []
    for response in root.findall(f"{{{DAV}}}response"):
        href = response.findtext(f"{{{DAV}}}href") or ""
        prop = response.find(f"{{{DAV}}}propstat/{{{DAV}}}prop")
        if prop is None:
            continue
        records.append(
            {
                "href": href,
                "display_name": prop.findtext(f"{{{DAV}}}displayname"),
                "content_length": prop.findtext(f"{{{DAV}}}getcontentlength"),
                "last_modified": prop.findtext(f"{{{DAV}}}getlastmodified"),
                "etag": prop.findtext(f"{{{DAV}}}getetag"),
                "is_collection": prop.find(f"{{{DAV}}}resourcetype/{{{DAV}}}collection") is not None,
                "source": "nextcloud_dav",
                "read_only": True,
            }
        )
        if len(records) > MAX_DAV_RECORDS:
            raise ValueError("Nextcloud DAV response contains too many records")
    return records


def parse_calendar_multistatus(data: bytes) -> list[dict]:
    root = _parse_xml(data, "calendar")
    records = []
    for response in root.findall(f"{{{DAV}}}response"):
        href = response.findtext(f"{{{DAV}}}href") or ""
        prop = response.find(f"{{{DAV}}}propstat/{{{DAV}}}prop")
        if prop is None:
            continue
        supported = {
            item.tag
            for item in prop.findall(f"{{{DAV}}}supported-report-set/{{{DAV}}}supported-report/{{{DAV}}}report/*")
        }
        records.append(
            {
                "href": href,
                "display_name": prop.findtext(f"{{{DAV}}}displayname"),
                "description": prop.findtext(f"{{{ICAL}}}calendar-description"),
                "timezone": prop.findtext(f"{{{ICAL}}}calendar-timezone"),
                "etag": prop.findtext(f"{{{DAV}}}getetag"),
                "last_modified": prop.findtext(f"{{{DAV}}}getlastmodified"),
                "is_collection": prop.find(f"{{{DAV}}}resourcetype/{{{DAV}}}collection") is not None,
                "is_calendar": prop.find(f"{{{DAV}}}resourcetype/{{{ICAL}}}calendar") is not None,
                "supports_calendar_query": f"{{{ICAL}}}calendar-query" in supported,
                "source": "nextcloud_caldav",
                "read_only": True,
            }
        )
        if len(records) > MAX_DAV_RECORDS:
            raise ValueError("Nextcloud calendar response contains too many records")
    return records


def parse_addressbook_multistatus(data: bytes) -> list[dict]:
    root = _parse_xml(data, "contacts")
    records = []
    for response in root.findall(f"{{{DAV}}}response"):
        href = response.findtext(f"{{{DAV}}}href") or ""
        prop = response.find(f"{{{DAV}}}propstat/{{{DAV}}}prop")
        if prop is None:
            continue
        supported = {
            item.tag
            for item in prop.findall(f"{{{DAV}}}supported-report-set/{{{DAV}}}supported-report/{{{DAV}}}report/*")
        }
        records.append(
            {
                "href": href,
                "display_name": prop.findtext(f"{{{DAV}}}displayname"),
                "description": prop.findtext(f"{{{CARDDAV}}}addressbook-description"),
                "etag": prop.findtext(f"{{{DAV}}}getetag"),
                "last_modified": prop.findtext(f"{{{DAV}}}getlastmodified"),
                "is_collection": prop.find(f"{{{DAV}}}resourcetype/{{{DAV}}}collection") is not None,
                "is_addressbook": prop.find(f"{{{DAV}}}resourcetype/{{{CARDDAV}}}addressbook") is not None,
                "supports_addressbook_query": f"{{{CARDDAV}}}addressbook-query" in supported,
                "source": "nextcloud_carddav",
                "read_only": True,
            }
        )
        if len(records) > MAX_DAV_RECORDS:
            raise ValueError("Nextcloud contacts response contains too many records")
    return records


def _first_text(prop: ET.Element, tag: str) -> str | None:
    value = prop.findtext(tag)
    return value if value not in {"", None} else None


def _unfold_lines(value: str) -> list[str]:
    lines: list[str] = []
    for raw in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ical_value(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    left, value = line.split(":", 1)
    return left.split(";", 1)[0].upper(), value.strip()


def _parse_ical_datetime(value: str) -> str:
    if not value:
        return value
    if value.endswith("Z") and len(value) >= 16:
        try:
            return dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).isoformat()
        except ValueError:
            return value
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            parsed = dt.datetime.strptime(value, fmt) if "T" in fmt else dt.datetime.strptime(value, fmt).date()
            return parsed.isoformat()
        except ValueError:
            continue
    return value


def _parse_vevent(calendar_data: str) -> dict:
    event = {}
    in_event = False
    for line in _unfold_lines(calendar_data):
        parsed = _ical_value(line)
        if parsed is None:
            continue
        key, value = parsed
        if key == "BEGIN" and value.upper() == "VEVENT":
            in_event = True
            continue
        if key == "END" and value.upper() == "VEVENT":
            break
        if not in_event:
            continue
        if key in {"UID", "SUMMARY", "LOCATION", "STATUS", "DESCRIPTION"}:
            event[key.lower()] = value[:2000]
        elif key in {"DTSTART", "DTEND", "CREATED", "LAST-MODIFIED"}:
            event[key.lower().replace("-", "_")] = _parse_ical_datetime(value)
    return event


def parse_calendar_events_multistatus(data: bytes) -> list[dict]:
    root = _parse_xml(data, "calendar events")
    records = []
    for response in root.findall(f"{{{DAV}}}response"):
        href = response.findtext(f"{{{DAV}}}href") or ""
        prop = response.find(f"{{{DAV}}}propstat/{{{DAV}}}prop")
        if prop is None:
            continue
        event = _parse_vevent(_first_text(prop, ICAL_DATA) or "")
        if not event:
            continue
        event.update(
            {
                "href": href,
                "etag": _first_text(prop, f"{{{DAV}}}getetag"),
                "source": "nextcloud_caldav",
                "read_only": True,
            }
        )
        records.append(event)
        if len(records) > MAX_DAV_RECORDS:
            raise ValueError("Nextcloud calendar event response contains too many records")
    return records


def _vcard_values(card_data: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for line in _unfold_lines(card_data):
        if ":" not in line:
            continue
        left, value = line.split(":", 1)
        key = left.split(";", 1)[0].upper()
        values.setdefault(key, []).append(value.strip())
    return values


def parse_address_cards_multistatus(data: bytes) -> list[dict]:
    root = _parse_xml(data, "contact cards")
    records = []
    for response in root.findall(f"{{{DAV}}}response"):
        href = response.findtext(f"{{{DAV}}}href") or ""
        prop = response.find(f"{{{DAV}}}propstat/{{{DAV}}}prop")
        if prop is None:
            continue
        values = _vcard_values(_first_text(prop, VCARD_DATA) or "")
        display_name = (values.get("FN") or [None])[0]
        if not display_name:
            continue
        records.append(
            {
                "href": href,
                "display_name": display_name[:500],
                "emails": [item[:500] for item in values.get("EMAIL", [])[:10]],
                "phones": [item[:100] for item in values.get("TEL", [])[:10]],
                "organization": (values.get("ORG") or [None])[0],
                "etag": _first_text(prop, f"{{{DAV}}}getetag"),
                "source": "nextcloud_carddav",
                "read_only": True,
            }
        )
        if len(records) > MAX_DAV_RECORDS:
            raise ValueError("Nextcloud contact response contains too many records")
    return records


def list_file_metadata(
    base_url: str,
    username: str,
    app_password: str,
    path: str,
    allowlist: list[str],
    timeout: int = 10,
    opener=urllib.request.urlopen,
) -> list[dict]:
    safe_path = validate_path(path, allowlist)
    encoded_user = urllib.parse.quote(username, safe="")
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in safe_path.split("/"))
    url = f"{validate_base_url(base_url)}/remote.php/dav/files/{encoded_user}/{encoded_path}"
    request = _propfind_request(
        url,
        _credential(username, app_password),
        '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
        "<d:displayname/><d:getcontentlength/><d:getlastmodified/><d:getetag/><d:resourcetype/>"
        "</d:prop></d:propfind>",
    )
    with opener(request, timeout=timeout) as response:
        return parse_multistatus(response.read())


def list_calendars(
    base_url: str,
    username: str,
    app_password: str,
    allowlist: list[str],
    timeout: int = 10,
    opener=urllib.request.urlopen,
) -> list[dict]:
    safe_names = [validate_name(name, allowlist, "calendar") for name in allowlist]
    encoded_user = urllib.parse.quote(username, safe="")
    url = f"{validate_base_url(base_url)}/remote.php/dav/calendars/{encoded_user}"
    request = _propfind_request(
        url,
        _credential(username, app_password),
        '<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        "<d:prop><d:displayname/><d:getetag/><d:getlastmodified/><d:resourcetype/>"
        "<d:supported-report-set/><c:calendar-description/><c:calendar-timezone/></d:prop></d:propfind>",
    )
    with opener(request, timeout=timeout) as response:
        records = parse_calendar_multistatus(response.read())
    return [record for record in records if (record.get("display_name") or "") in safe_names]


def list_calendar_events(
    base_url: str,
    username: str,
    app_password: str,
    calendar_name: str,
    allowlist: list[str],
    start: str,
    end: str,
    timeout: int = 10,
    opener=urllib.request.urlopen,
) -> list[dict]:
    safe_name = validate_name(calendar_name, allowlist, "calendar")
    calendars = list_calendars(base_url, username, app_password, [safe_name], timeout=timeout, opener=opener)
    if not calendars:
        return []
    url = _href_url(base_url, calendars[0].get("href") or "", "calendar")
    request = _report_request(
        url,
        _credential(username, app_password),
        '<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
        "<c:filter><c:comp-filter name=\"VCALENDAR\"><c:comp-filter name=\"VEVENT\">"
        f"<c:time-range start=\"{start}\" end=\"{end}\"/>"
        "</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>",
    )
    with opener(request, timeout=timeout) as response:
        return parse_calendar_events_multistatus(response.read())


def list_addressbooks(
    base_url: str,
    username: str,
    app_password: str,
    allowlist: list[str],
    timeout: int = 10,
    opener=urllib.request.urlopen,
) -> list[dict]:
    safe_names = [validate_name(name, allowlist, "address book") for name in allowlist]
    encoded_user = urllib.parse.quote(username, safe="")
    url = f"{validate_base_url(base_url)}/remote.php/dav/addressbooks/users/{encoded_user}"
    request = _propfind_request(
        url,
        _credential(username, app_password),
        '<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:a="urn:ietf:params:xml:ns:carddav">'
        "<d:prop><d:displayname/><d:getetag/><d:getlastmodified/><d:resourcetype/>"
        "<d:supported-report-set/><a:addressbook-description/></d:prop></d:propfind>",
    )
    with opener(request, timeout=timeout) as response:
        records = parse_addressbook_multistatus(response.read())
    return [record for record in records if (record.get("display_name") or "") in safe_names]


def list_address_cards(
    base_url: str,
    username: str,
    app_password: str,
    addressbook_name: str,
    allowlist: list[str],
    timeout: int = 10,
    opener=urllib.request.urlopen,
) -> list[dict]:
    safe_name = validate_name(addressbook_name, allowlist, "address book")
    addressbooks = list_addressbooks(base_url, username, app_password, [safe_name], timeout=timeout, opener=opener)
    if not addressbooks:
        return []
    url = _href_url(base_url, addressbooks[0].get("href") or "", "address book")
    request = _report_request(
        url,
        _credential(username, app_password),
        '<?xml version="1.0"?><a:addressbook-query xmlns:d="DAV:" xmlns:a="urn:ietf:params:xml:ns:carddav">'
        "<d:prop><d:getetag/><a:address-data/></d:prop></a:addressbook-query>",
    )
    with opener(request, timeout=timeout) as response:
        return parse_address_cards_multistatus(response.read())
