#!/usr/bin/env python3

import atlas_nextcloud as nc


DAV_FIXTURE = b'''<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/remote.php/dav/files/atlas-reader/ATLAS/</d:href><d:propstat><d:prop>
    <d:displayname>ATLAS</d:displayname><d:resourcetype><d:collection/></d:resourcetype>
  </d:prop></d:propstat></d:response>
  <d:response><d:href>/remote.php/dav/files/atlas-reader/ATLAS/syllabus.pdf</d:href><d:propstat><d:prop>
    <d:displayname>syllabus.pdf</d:displayname><d:getcontentlength>1234</d:getcontentlength>
    <d:getlastmodified>Sat, 29 Aug 2026 18:00:00 GMT</d:getlastmodified><d:getetag>synthetic-etag</d:getetag>
    <d:resourcetype/>
  </d:prop></d:propstat></d:response>
</d:multistatus>'''

CALENDAR_FIXTURE = b'''<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response><d:href>/nextcloud/remote.php/dav/calendars/atlas-reader/Classes/</d:href><d:propstat><d:prop>
    <d:displayname>Classes</d:displayname><c:calendar-description>School items</c:calendar-description>
    <c:calendar-timezone>America/Los_Angeles</c:calendar-timezone><d:getetag>"classes-etag"</d:getetag>
    <d:getlastmodified>Sat, 29 Aug 2026 18:05:00 GMT</d:getlastmodified>
    <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
    <d:supported-report-set><d:supported-report><d:report><c:calendar-query/></d:report></d:supported-report></d:supported-report-set>
  </d:prop></d:propstat></d:response>
  <d:response><d:href>/nextcloud/remote.php/dav/calendars/atlas-reader/Private/</d:href><d:propstat><d:prop>
    <d:displayname>Private</d:displayname><d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
  </d:prop></d:propstat></d:response>
</d:multistatus>'''

ADDRESSBOOK_FIXTURE = b'''<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:a="urn:ietf:params:xml:ns:carddav">
  <d:response><d:href>/nextcloud/remote.php/dav/addressbooks/users/atlas-reader/Family/</d:href><d:propstat><d:prop>
    <d:displayname>Family</d:displayname><a:addressbook-description>Family contacts</a:addressbook-description>
    <d:getetag>"family-etag"</d:getetag><d:getlastmodified>Sat, 29 Aug 2026 18:10:00 GMT</d:getlastmodified>
    <d:resourcetype><d:collection/><a:addressbook/></d:resourcetype>
    <d:supported-report-set><d:supported-report><d:report><a:addressbook-query/></d:report></d:supported-report></d:supported-report-set>
  </d:prop></d:propstat></d:response>
  <d:response><d:href>/nextcloud/remote.php/dav/addressbooks/users/atlas-reader/All/</d:href><d:propstat><d:prop>
    <d:displayname>All</d:displayname><d:resourcetype><d:collection/><a:addressbook/></d:resourcetype>
  </d:prop></d:propstat></d:response>
</d:multistatus>'''

CALENDAR_EVENTS_FIXTURE = b'''<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response><d:href>/nextcloud/remote.php/dav/calendars/atlas-reader/Classes/event-1.ics</d:href><d:propstat><d:prop>
    <d:getetag>"event-etag"</d:getetag>
    <c:calendar-data>BEGIN:VCALENDAR
BEGIN:VEVENT
UID:event-1
SUMMARY:Math lecture
DTSTART:20260830T170000Z
DTEND:20260830T180000Z
LOCATION:Room 101
STATUS:CONFIRMED
DESCRIPTION:Bring notebook
END:VEVENT
END:VCALENDAR</c:calendar-data>
  </d:prop></d:propstat></d:response>
</d:multistatus>'''

CONTACT_CARDS_FIXTURE = b'''<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:a="urn:ietf:params:xml:ns:carddav">
  <d:response><d:href>/nextcloud/remote.php/dav/addressbooks/users/atlas-reader/Family/alex.vcf</d:href><d:propstat><d:prop>
    <d:getetag>"contact-etag"</d:getetag>
    <a:address-data>BEGIN:VCARD
VERSION:4.0
FN:Alex Example
EMAIL:alex@example.test
TEL:+15550101010
ORG:Example Org
END:VCARD</a:address-data>
  </d:prop></d:propstat></d:response>
</d:multistatus>'''


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return DAV_FIXTURE


class CalendarResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return CALENDAR_FIXTURE


class AddressbookResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return ADDRESSBOOK_FIXTURE


class BytesResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.data


def main() -> None:
    assert nc.validate_base_url("https://docker-main.example.ts.net/nextcloud")
    for unsafe in ("http://192.168.1.60/nextcloud", "https://example.com/nextcloud", "file:///tmp/nextcloud"):
        try:
            nc.validate_base_url(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe Nextcloud endpoint must be rejected")
    assert nc.validate_path("ATLAS/Courses", ["ATLAS"]) == "ATLAS/Courses"
    for unsafe_path in ("Private", "ATLAS/../Private"):
        try:
            nc.validate_path(unsafe_path, ["ATLAS"])
        except PermissionError:
            pass
        else:
            raise AssertionError("non-allowlisted Nextcloud path must be rejected")

    seen = {}

    def opener(request, timeout):
        seen["method"] = request.method
        seen["depth"] = request.headers.get("Depth")
        seen["url"] = request.full_url
        return Response()

    records = nc.list_file_metadata(
        "https://docker-main.example.ts.net/nextcloud",
        "atlas-reader",
        "synthetic-app-password",
        "ATLAS",
        ["ATLAS"],
        opener=opener,
    )
    assert seen["method"] == "PROPFIND"
    assert seen["depth"] == "1"
    assert records[0]["is_collection"] is True
    assert records[1]["display_name"] == "syllabus.pdf"
    assert records[1]["read_only"] is True
    calendar_seen = {}

    def calendar_opener(request, timeout):
        calendar_seen["url"] = request.full_url
        return CalendarResponse()

    calendars = nc.list_calendars(
        "https://docker-main.example.ts.net/nextcloud",
        "atlas-reader",
        "synthetic-app-password",
        ["Classes"],
        opener=calendar_opener,
    )
    assert calendar_seen["url"].endswith("/remote.php/dav/calendars/atlas-reader")
    assert len(calendars) == 1
    assert calendars[0]["display_name"] == "Classes"
    assert calendars[0]["is_calendar"] is True
    assert calendars[0]["supports_calendar_query"] is True

    addressbook_seen = {}

    def addressbook_opener(request, timeout):
        addressbook_seen["url"] = request.full_url
        return AddressbookResponse()

    addressbooks = nc.list_addressbooks(
        "https://docker-main.example.ts.net/nextcloud",
        "atlas-reader",
        "synthetic-app-password",
        ["Family"],
        opener=addressbook_opener,
    )
    assert addressbook_seen["url"].endswith("/remote.php/dav/addressbooks/users/atlas-reader")
    assert len(addressbooks) == 1
    assert addressbooks[0]["display_name"] == "Family"
    assert addressbooks[0]["is_addressbook"] is True
    assert addressbooks[0]["supports_addressbook_query"] is True
    event_seen = []

    def event_opener(request, timeout):
        event_seen.append((request.method, request.full_url))
        if request.method == "PROPFIND":
            return BytesResponse(CALENDAR_FIXTURE)
        return BytesResponse(CALENDAR_EVENTS_FIXTURE)

    events = nc.list_calendar_events(
        "https://docker-main.example.ts.net/nextcloud",
        "atlas-reader",
        "synthetic-app-password",
        "Classes",
        ["Classes"],
        "20260830T000000Z",
        "20260831T000000Z",
        opener=event_opener,
    )
    assert event_seen[1][0] == "REPORT"
    assert event_seen[1][1].endswith("/remote.php/dav/calendars/atlas-reader/Classes/")
    assert events[0]["summary"] == "Math lecture"
    assert events[0]["dtstart"] == "2026-08-30T17:00:00+00:00"
    assert events[0]["read_only"] is True
    contact_seen = []

    def contact_opener(request, timeout):
        contact_seen.append((request.method, request.full_url))
        if request.method == "PROPFIND":
            return BytesResponse(ADDRESSBOOK_FIXTURE)
        return BytesResponse(CONTACT_CARDS_FIXTURE)

    contacts = nc.list_address_cards(
        "https://docker-main.example.ts.net/nextcloud",
        "atlas-reader",
        "synthetic-app-password",
        "Family",
        ["Family"],
        opener=contact_opener,
    )
    assert contact_seen[1][0] == "REPORT"
    assert contact_seen[1][1].endswith("/remote.php/dav/addressbooks/users/atlas-reader/Family/")
    assert contacts[0]["display_name"] == "Alex Example"
    assert contacts[0]["emails"] == ["alex@example.test"]
    assert contacts[0]["phones"] == ["+15550101010"]
    try:
        nc.list_calendar_events(
            "https://docker-main.example.ts.net/nextcloud",
            "atlas-reader",
            "synthetic-app-password",
            "Private",
            ["Classes"],
            "20260830T000000Z",
            "20260831T000000Z",
            opener=event_opener,
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("non-allowlisted calendar event read must be rejected")
    assert not hasattr(nc, "put_file")
    assert not hasattr(nc, "delete_file")
    print("ATLAS Nextcloud smoke test passed")


if __name__ == "__main__":
    main()
