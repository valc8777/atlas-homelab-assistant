#!/usr/bin/env python3
"""AVRCP MediaPlayer1 listener for the ATLAS home-speaker hardware buttons.

DEC-2026-09-08-01 gave ATLAS a physical voice-output device: a 2Boom
Bluetooth speaker paired to `ai-stack`, with Piper TTS audio played through
it via `ffplay` inside the `atlas-shortcuts` container. This script closes
the loop on the speaker's own transport buttons (Play/Pause/Stop/Next/Prev):
when paired for A2DP audio, those buttons don't control anything locally on
the speaker -- they send AVRCP remote-control commands back to whichever
device it is paired to (the same mechanism headphone play/pause buttons use
to control a phone's music app). Nothing on `ai-stack` was listening for
those commands before this script existed, so the buttons did nothing.

Scope is deliberately narrow: TTS playback (`ffplay`, one-shot, no pause/
resume concept) is the only thing this controls. Play, Pause, and Stop all
map to the same action -- interrupt any in-progress speech immediately --
because a single physical button commonly sends a toggling "play/pause"
AVRCP command rather than distinct press events, and there is no partial
state to resume for a one-shot TTS utterance. Next/Previous are logged only;
there is no track concept for spoken responses.

Runs on the `ai-stack` host itself (not inside the `atlas-shortcuts`
container), because it needs the system D-Bus connection BlueZ's AVRCP
target implementation uses (`org.bluez.Media1.RegisterPlayer`), which is a
different bus than anything reasonable to expose into a container. It reaches
into the `atlas-shortcuts` container the same way any host operator would --
`docker exec ... pkill ffplay` -- rather than requiring an application-level
stop endpoint, since `subprocess.run()` in `play_audio_on_home_speaker()`
already tolerates its child process being killed out from under it (no
exception, no effect on the HTTP response already sent to the original
caller).

Deploy: copied to the `ai-stack` host (not into any container) and run as a
systemd --user service under the `cerebro` account, alongside the
`pulseaudio` user service this depends on. See homelab-change-log.md for the
exact deployment commands used.
"""
import subprocess
import sys

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

ADAPTER_PATH = "/org/bluez/hci0"
PLAYER_PATH = "/atlas/avrcp_player"
HOME_SPEAKER_CONTAINER = "atlas-shortcuts"


def stop_home_speaker_playback(reason: str) -> None:
    """Best-effort: kill any in-progress ffplay TTS playback. Never raises."""
    try:
        result = subprocess.run(
            ["docker", "exec", HOME_SPEAKER_CONTAINER, "pkill", "-x", "ffplay"],
            capture_output=True,
            timeout=10,
        )
        # pkill returns 1 when no matching process exists -- not an error
        # here, it just means nothing was playing when the button was hit.
        sys.stderr.write(
            f"atlas-avrcp: {reason} -> pkill ffplay rc={result.returncode}\n"
        )
    except Exception as exc:  # pragma: no cover -- best effort, never fatal
        sys.stderr.write(f"atlas-avrcp: failed to stop playback ({reason}): {exc}\n")


class AtlasAvrcpPlayer(dbus.service.Object):
    """Minimal org.bluez.MediaPlayer1 target.

    Only implements what BlueZ needs to complete AVRCP registration and to
    route incoming transport-control commands to us; this is not a real
    media player and does not track genuine playback position/track state.
    """

    def _properties(self) -> dict:
        return {
            "Equalizer": "off",
            "Repeat": "off",
            "Shuffle": "off",
            "Status": "paused",
            "Position": dbus.UInt32(0),
            "Track": dbus.Dictionary({}, signature="sv"),
        }

    @dbus.service.method(
        "org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}"
    )
    def GetAll(self, interface):
        if interface == "org.bluez.MediaPlayer1":
            return self._properties()
        return {}

    @dbus.service.method(
        "org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v"
    )
    def Get(self, interface, prop):
        return self._properties().get(prop, "")

    @dbus.service.method("org.bluez.MediaPlayer1")
    def Play(self):
        stop_home_speaker_playback("Play button")

    @dbus.service.method("org.bluez.MediaPlayer1")
    def Pause(self):
        stop_home_speaker_playback("Pause button")

    @dbus.service.method("org.bluez.MediaPlayer1")
    def Stop(self):
        stop_home_speaker_playback("Stop button")

    @dbus.service.method("org.bluez.MediaPlayer1")
    def Next(self):
        sys.stderr.write("atlas-avrcp: Next received (no-op -- no track concept for TTS)\n")

    @dbus.service.method("org.bluez.MediaPlayer1")
    def Previous(self):
        sys.stderr.write("atlas-avrcp: Previous received (no-op -- no track concept for TTS)\n")


def main() -> None:
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    AtlasAvrcpPlayer(bus, PLAYER_PATH)

    media = dbus.Interface(bus.get_object("org.bluez", ADAPTER_PATH), "org.bluez.Media1")
    try:
        media.RegisterPlayer(dbus.ObjectPath(PLAYER_PATH), dbus.Dictionary({}, signature="sv"))
    except dbus.exceptions.DBusException as exc:
        sys.stderr.write(f"atlas-avrcp: RegisterPlayer failed: {exc}\n")
        raise

    sys.stderr.write("atlas-avrcp: registered as MediaPlayer1 on " + ADAPTER_PATH + ", listening\n")
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
