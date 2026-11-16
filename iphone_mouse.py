#!/usr/bin/env python3
# Copyright (c) 2026 PANKKY
# Licensed under the MIT License - see the LICENSE file.
# https://github.com/P4NKK-TRYHARD/iPhone-Mirror-for-Linux
"""
iphone_mouse.py - Use your laptop's mouse, touchpad and keyboard as a Bluetooth
mouse and keyboard for your iPhone. Pair it with UxPlay to see the iPhone's
screen on your laptop and control it from there.

Usage:
  sudo python3 iphone_mouse.py setup   # once (adjusts the Bluetooth service)
  sudo python3 iphone_mouse.py         # start
  sudo python3 iphone_mouse.py undo    # restore the Bluetooth service

While it is running, press and release RIGHT CTRL to hand the mouse and
keyboard over to the iPhone, and press it again to get them back.

Requirements on Fedora (usually already installed):
  sudo dnf install python3-dbus python3-gobject bluez
Debian/Ubuntu:
  sudo apt install python3-dbus python3-gi bluez
"""

import argparse
import fcntl
import os
import re
import select
import socket
import struct
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UUID_HID = "00001124-0000-1000-8000-00805f9b34fb"
PSM_CTRL = 0x11
PSM_INTR = 0x13
DEFAULT_NAME = "Linux Mouse"
INTERVAL = 0.012   # seconds between movement reports (~80 per second)

DROPIN_DIR = "/etc/systemd/system/bluetooth.service.d"
DROPIN = os.path.join(DROPIN_DIR, "iphone-mouse.conf")
LEGACY_DROPINS = [os.path.join(DROPIN_DIR, "raton-iphone.conf")]
STATE_DIR = "/var/lib/iphone-mouse"
LAST_DEVICE = os.path.join(STATE_DIR, "last_iphone")

# HID descriptor: keyboard (ID 1, with LEDs) + 3-button mouse with wheel (ID 2)
HID_DESCRIPTOR = (
    # Keyboard
    "05010906a101"
    "8501"
    "050719e029e71500250175019508" "8102"   # modifiers
    "95017508" "8101"                       # reserved
    "95057501050819012905" "9102"           # LEDs
    "95017503" "9101"                       # LED padding
    "95067508150025650507190029658100"      # 6 keys
    "c0"
    # Mouse
    "05010902a101"
    "8502"
    "0901a100"
    "0509190129031500250195037501" "8102"   # 3 buttons
    "95017505" "8103"                       # padding
    "05010930093109381581257f75089503" "8106"  # X, Y, wheel (relative)
    "c0c0"
)

SDP_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001"><sequence><uuid value="0x1124" /></sequence></attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence><uuid value="0x0100" /><uint16 value="0x0011" /></sequence>
      <sequence><uuid value="0x0011" /></sequence>
    </sequence>
  </attribute>
  <attribute id="0x0005"><sequence><uuid value="0x1002" /></sequence></attribute>
  <attribute id="0x0006">
    <sequence><uint16 value="0x656e" /><uint16 value="0x006a" /><uint16 value="0x0100" /></sequence>
  </attribute>
  <attribute id="0x0009">
    <sequence><sequence><uuid value="0x1124" /><uint16 value="0x0101" /></sequence></sequence>
  </attribute>
  <attribute id="0x000d">
    <sequence>
      <sequence>
        <sequence><uuid value="0x0100" /><uint16 value="0x0013" /></sequence>
        <sequence><uuid value="0x0011" /></sequence>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0100"><text value="{name}" /></attribute>
  <attribute id="0x0101"><text value="Mouse and keyboard" /></attribute>
  <attribute id="0x0102"><text value="Linux" /></attribute>
  <attribute id="0x0200"><uint16 value="0x0100" /></attribute>
  <attribute id="0x0201"><uint16 value="0x0111" /></attribute>
  <attribute id="0x0202"><uint8 value="0xc0" /></attribute>
  <attribute id="0x0203"><uint8 value="0x00" /></attribute>
  <attribute id="0x0204"><boolean value="true" /></attribute>
  <attribute id="0x0205"><boolean value="true" /></attribute>
  <attribute id="0x0206">
    <sequence><sequence><uint8 value="0x22" /><text encoding="hex" value="{descriptor}" /></sequence></sequence>
  </attribute>
  <attribute id="0x0207">
    <sequence><sequence><uint16 value="0x0409" /><uint16 value="0x0100" /></sequence></sequence>
  </attribute>
  <attribute id="0x020b"><uint16 value="0x0100" /></attribute>
  <attribute id="0x020c"><uint16 value="0x0c80" /></attribute>
  <attribute id="0x020d"><boolean value="false" /></attribute>
  <attribute id="0x020e"><boolean value="true" /></attribute>
  <attribute id="0x020f"><uint16 value="0x0640" /></attribute>
  <attribute id="0x0210"><uint16 value="0x0320" /></attribute>
</record>
"""

# evdev
EV_SYN, EV_KEY, EV_REL, EV_ABS, EV_REP = 0x00, 0x01, 0x02, 0x03, 0x14
REL_X, REL_Y, REL_WHEEL = 0x00, 0x01, 0x08
ABS_X, ABS_Y = 0x00, 0x01
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_TOOL_FINGER, BTN_TOUCH = 0x145, 0x14A
BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP, BTN_TOOL_QUADTAP = 0x14D, 0x14E, 0x14F
KEY_A, KEY_SPACE, KEY_RIGHTCTRL = 30, 57, 97
EVIOCGRAB = 0x40044590
EVENT = struct.Struct("llHHi")


def eviocgabs(axis):
    # _IOR('E', 0x40 + axis, struct input_absinfo)  (6 ints = 24 bytes)
    return (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + axis)


# evdev key codes -> HID usage codes
KEYS = {
    1: 0x29, 14: 0x2A, 15: 0x2B, 28: 0x28, 57: 0x2C,
    12: 0x2D, 13: 0x2E, 26: 0x2F, 27: 0x30, 43: 0x32, 39: 0x33, 40: 0x34,
    41: 0x35, 51: 0x36, 52: 0x37, 53: 0x38, 58: 0x39, 86: 0x64,
    102: 0x4A, 103: 0x52, 104: 0x4B, 105: 0x50, 106: 0x4F, 107: 0x4D,
    108: 0x51, 109: 0x4E, 110: 0x49, 111: 0x4C,
    87: 0x44, 88: 0x45, 99: 0x46, 119: 0x48,
    113: 0x7F, 114: 0x81, 115: 0x80,  # mute, volume down, volume up
}
for _i, _k in enumerate(range(2, 11)):          # 1..9
    KEYS[_k] = 0x1E + _i
KEYS[11] = 0x27                                 # 0
for _i, _k in enumerate(range(59, 69)):         # F1..F10
    KEYS[_k] = 0x3A + _i
for _letter, _k in zip("qwertyuiop", range(16, 26)):
    KEYS[_k] = 0x04 + ord(_letter) - ord("a")
for _letter, _k in zip("asdfghjkl", range(30, 39)):
    KEYS[_k] = 0x04 + ord(_letter) - ord("a")
for _letter, _k in zip("zxcvbnm", range(44, 51)):
    KEYS[_k] = 0x04 + ord(_letter) - ord("a")

MODIFIERS = {29: 0x01, 42: 0x02, 56: 0x04, 125: 0x08,
             97: 0x10, 54: 0x20, 100: 0x40, 126: 0x80}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fail(msg):
    print(f"\n✗ {msg}\n", file=sys.stderr)
    sys.exit(1)


def require_root():
    if os.geteuid() != 0:
        fail("This needs to run with sudo:  sudo python3 " + " ".join(sys.argv))


def run(*cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def bluetoothd_path():
    for p in ("/usr/libexec/bluetooth/bluetoothd", "/usr/lib/bluetooth/bluetoothd",
              "/usr/sbin/bluetoothd"):
        if os.path.exists(p):
            return p
    fail("Can't find bluetoothd. Install BlueZ (e.g. sudo dnf install bluez).")


def input_plugin_disabled():
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if args and args[0].endswith(b"bluetoothd"):
            joined = b" ".join(args)
            return b"-P input" in joined or b"--noplugin=input" in joined or b"-Pinput" in joined
    return False


def setup():
    require_root()
    bt = bluetoothd_path()
    os.makedirs(DROPIN_DIR, exist_ok=True)
    with open(DROPIN, "w") as f:
        f.write("# Created by iphone_mouse.py. Delete it (or run 'undo') to revert.\n"
                f"[Service]\nExecStart=\nExecStart={bt} -P input\n")
    run("systemctl", "daemon-reload")
    run("systemctl", "restart", "bluetooth")
    time.sleep(2)
    print("✓ Bluetooth is ready.")
    print("  Note: while it's set up like this, Bluetooth mice/keyboards used BY THIS")
    print("  laptop won't work. To go back to normal:  sudo python3 iphone_mouse.py undo")


def undo():
    require_root()
    for path in [DROPIN] + LEGACY_DROPINS:
        if os.path.exists(path):
            os.remove(path)
    run("systemctl", "daemon-reload")
    run("systemctl", "restart", "bluetooth")
    print("✓ Bluetooth restored to its normal configuration.")


# ---------------------------------------------------------------------------
# Laptop input devices
# ---------------------------------------------------------------------------


def _bit(bitmap, n):
    words = bitmap.split()[::-1]
    idx, off = divmod(n, 64)
    if idx >= len(words):
        return False
    return bool((int(words[idx], 16) >> off) & 1)


def find_devices(text=None):
    if text is None:
        with open("/proc/bus/input/devices") as f:
            text = f.read()
    keyboards, mice, touchpads = [], [], []
    for block in text.strip().split("\n\n"):
        name, event, bits = "", None, {}
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line[8:].strip().strip('"')
            elif line.startswith("H: Handlers="):
                m = re.search(r"\bevent(\d+)\b", line)
                if m:
                    event = f"/dev/input/event{m.group(1)}"
            elif line.startswith("B: "):
                k, _, v = line[3:].partition("=")
                bits[k] = v
        if not event:
            continue
        ev, key = bits.get("EV", "0"), bits.get("KEY", "0")
        rel, abs_ = bits.get("REL", "0"), bits.get("ABS", "0")
        if (_bit(ev, EV_ABS) and _bit(abs_, ABS_X) and _bit(abs_, ABS_Y)
                and _bit(key, BTN_TOOL_FINGER)):
            touchpads.append((event, name))
        elif _bit(ev, EV_REL) and _bit(rel, REL_X) and _bit(rel, REL_Y) and _bit(key, BTN_LEFT):
            mice.append((event, name))
        elif _bit(ev, EV_KEY) and _bit(ev, EV_REP) and _bit(key, KEY_A) and _bit(key, KEY_SPACE):
            keyboards.append((event, name))
    return keyboards, mice, touchpads


# ---------------------------------------------------------------------------
# HID connection to the iPhone
# ---------------------------------------------------------------------------


class HIDConnection:
    def __init__(self):
        self.ctrl = None
        self.intr = None
        self.address = None
        self.lock = threading.Lock()
        self.changed = threading.Event()
        self.on_change = None   # function(connected: bool)

    @property
    def connected(self):
        return self.intr is not None

    def attach(self, ctrl, intr, address):
        with self.lock:
            if self.intr is not None:
                for s in (ctrl, intr):
                    try:
                        s.close()
                    except OSError:
                        pass
                return False
            self.ctrl, self.intr, self.address = ctrl, intr, address
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(LAST_DEVICE, "w") as f:
                f.write(address)
        except OSError:
            pass
        print(f"\n✓ iPhone connected ({address}). Press RIGHT CTRL to control it.")
        if self.on_change:
            self.on_change(True)
        threading.Thread(target=self._serve_control, args=(ctrl,), daemon=True).start()
        threading.Thread(target=self._drain_interrupt, args=(intr,), daemon=True).start()
        self.changed.set()
        return True

    def close(self):
        with self.lock:
            sockets = (self.ctrl, self.intr)
            if self.intr is None:
                return
            self.ctrl = self.intr = None
        for s in sockets:
            try:
                s.close()
            except OSError:
                pass
        print("\n· iPhone disconnected. Waiting for it to reconnect…")
        if self.on_change:
            self.on_change(False)
        self.changed.set()

    def send(self, data):
        with self.lock:
            s = self.intr
        if s is None:
            return False
        try:
            s.send(data)
            return True
        except OSError:
            self.close()
            return False

    def _serve_control(self, s):
        while True:
            try:
                d = s.recv(64)
            except OSError:
                break
            if not d:
                break
            kind, param = d[0] >> 4, d[0] & 0x0F
            try:
                if kind == 0x1:                       # HID_CONTROL
                    if param == 0x5:                  # VIRTUAL_CABLE_UNPLUG
                        break
                elif kind in (0x5, 0x7):              # SET_REPORT / SET_PROTOCOL
                    s.send(b"\x00")
                elif kind == 0x6:                     # GET_PROTOCOL
                    s.send(b"\xa0\x01")
                elif kind == 0x4:                     # GET_REPORT
                    rid = d[1] if len(d) > 1 else 0
                    if rid == 1:
                        s.send(b"\xa1\x01" + bytes(8))
                    elif rid == 2:
                        s.send(b"\xa1\x02" + bytes(4))
                    else:
                        s.send(b"\x02")               # ERR_INVALID_REPORT_ID
                else:
                    s.send(b"\x03")                   # ERR_UNSUPPORTED_REQUEST
            except OSError:
                break
        self.close()

    @staticmethod
    def _drain_interrupt(s):
        while True:
            try:
                if not s.recv(64):
                    break
            except OSError:
                break


def _l2cap_socket():
    return socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)


def server(conn):
    sc, si = _l2cap_socket(), _l2cap_socket()
    for s, psm in ((sc, PSM_CTRL), (si, PSM_INTR)):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((socket.BDADDR_ANY, psm))
        except OSError as e:
            fail(f"Can't open the HID channel ({e}). Did you run 'setup'?")
        s.listen(1)
    while True:
        try:
            ctrl, peer = sc.accept()
        except OSError:
            time.sleep(1)
            continue
        address = peer[0]
        si.settimeout(10)
        try:
            intr, _ = si.accept()
        except OSError:
            ctrl.close()
            continue
        finally:
            si.settimeout(None)
        conn.attach(ctrl, intr, address)


def reconnect(conn):
    """If an iPhone was paired before, try to connect to it ourselves."""
    while True:
        if not conn.connected and os.path.exists(LAST_DEVICE):
            ctrl = intr = None
            try:
                with open(LAST_DEVICE) as f:
                    address = f.read().strip()
                ctrl, intr = _l2cap_socket(), _l2cap_socket()
                ctrl.settimeout(5)
                intr.settimeout(5)
                ctrl.connect((address, PSM_CTRL))
                intr.connect((address, PSM_INTR))
                ctrl.settimeout(None)
                intr.settimeout(None)
                conn.attach(ctrl, intr, address)
            except OSError:
                for s in (ctrl, intr):
                    try:
                        s and s.close()
                    except OSError:
                        pass
        conn.changed.wait(8)
        conn.changed.clear()


# ---------------------------------------------------------------------------
# BlueZ over D-Bus: HID profile, pairing agent and adapter
# ---------------------------------------------------------------------------


def start_bluez(name, conn):
    try:
        import dbus
        import dbus.service
        import dbus.mainloop.glib
        from gi.repository import GLib
    except ImportError:
        fail("Missing libraries. Install them with:\n"
             "    Fedora:        sudo dnf install python3-dbus python3-gobject\n"
             "    Debian/Ubuntu: sudo apt install python3-dbus python3-gi")

    if hasattr(dbus.mainloop.glib, "threads_init"):
        dbus.mainloop.glib.threads_init()
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    def trust(path):
        try:
            props = dbus.Interface(bus.get_object("org.bluez", path),
                                   "org.freedesktop.DBus.Properties")
            props.Set("org.bluez.Device1", "Trusted", dbus.Boolean(True))
        except Exception:
            pass

    class Profile(dbus.service.Object):
        @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
        def NewConnection(self, device, fd, props):
            try:
                os.close(fd.take())
            except Exception:
                pass

        @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
        def RequestDisconnection(self, device):
            pass

    class Agent(dbus.service.Object):
        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def AuthorizeService(self, device, uuid):
            trust(device)

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
        def RequestPinCode(self, device):
            trust(device)
            return "0000"

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
        def RequestPasskey(self, device):
            trust(device)
            return dbus.UInt32(0)

        @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
        def DisplayPasskey(self, device, passkey, entered):
            print(f"  Pairing code: {passkey:06d}")

        @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def DisplayPinCode(self, device, pin):
            print(f"  Pairing PIN: {pin}")

        @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
        def RequestConfirmation(self, device, passkey):
            print(f"  Pairing (code {passkey:06d})… accepted.")
            trust(device)

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
        def RequestAuthorization(self, device):
            trust(device)

        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Cancel(self):
            pass

    # Adapter
    om = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
    adapter = None
    for path, ifaces in om.GetManagedObjects().items():
        if "org.bluez.Adapter1" in ifaces:
            adapter = path
            break
    if not adapter:
        fail("No Bluetooth adapter found. Is the laptop's Bluetooth turned on?")

    profile = Profile(bus, "/iphone_mouse/profile")
    agent = Agent(bus, "/iphone_mouse/agent")

    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
    xml = SDP_XML.format(name=name, descriptor=HID_DESCRIPTOR)
    try:
        manager.RegisterProfile("/iphone_mouse/profile", UUID_HID, {
            "ServiceRecord": xml,
            "Role": "server",
            "RequireAuthentication": dbus.Boolean(False),
            "RequireAuthorization": dbus.Boolean(False),
        })
    except dbus.exceptions.DBusException as e:
        fail(f"BlueZ rejected the HID profile: {e.get_dbus_message()}")

    agents = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
    agents.RegisterAgent("/iphone_mouse/agent", "NoInputNoOutput")
    agents.RequestDefaultAgent("/iphone_mouse/agent")

    props = dbus.Interface(bus.get_object("org.bluez", adapter), "org.freedesktop.DBus.Properties")
    props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
    props.Set("org.bluez.Adapter1", "Alias", name)
    props.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(0))
    props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(True))
    props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(0))
    props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(True))

    # Advertise as a keyboard/mouse peripheral instead of a computer
    index = adapter.rsplit("hci", 1)[-1]
    r = run("btmgmt", "--index", index, "class", "5", "192")
    if r.returncode != 0:
        print("  (Note: couldn't change the Bluetooth device class with btmgmt; "
              "it usually works anyway.)")

    def on_change(connected):
        # Stop advertising while connected (less interference with Wi-Fi/AirPlay)
        try:
            props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(not connected))
        except Exception:
            pass
    conn.on_change = on_change

    threading.Thread(target=GLib.MainLoop().run, daemon=True).start()
    # Keep references alive
    return profile, agent


# ---------------------------------------------------------------------------
# Reading the laptop's keyboard, mouse and touchpad
# ---------------------------------------------------------------------------


class Input:
    def __init__(self, conn, speed, toggle_key, invert_scroll):
        self.conn = conn
        self.speed = speed
        self.toggle_key = toggle_key
        self.invert = -1 if invert_scroll else 1
        self.active = False
        self.fds = {}            # fd -> (path, kind, name)
        self.absinfo = {}        # fd -> (resolution_x, resolution_y)
        # keyboard state
        self.mods = 0
        self.pressed = []
        # mouse state
        self.buttons = 0
        self.dx = self.dy = self.wheel = 0
        self.rem_x = self.rem_y = self.rem_wheel = 0.0
        self.pend_x = self.pend_y = self.pend_w = 0
        self.last_sent = 0.0
        # touchpad state
        self.tp = {}

    def open(self, keyboards, mice, touchpads):
        for devices, kind in ((keyboards, "keyboard"), (mice, "mouse"), (touchpads, "touchpad")):
            for path, name in devices:
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                except OSError:
                    continue
                self.fds[fd] = (path, kind, name)
                print(f"  · {kind:9s} {name}")
                if kind == "touchpad":
                    self.absinfo[fd] = (self._resolution(fd, ABS_X), self._resolution(fd, ABS_Y))
                    self.tp[fd] = dict(touching=False, x=None, y=None, fingers=0, max_fingers=0,
                                       start=0.0, travel=0.0, clicked=False)
        if not any(k == "keyboard" for _, k, _ in self.fds.values()):
            fail("Can't find the laptop's keyboard.")

    @staticmethod
    def _resolution(fd, axis):
        buf = bytearray(24)
        try:
            fcntl.ioctl(fd, eviocgabs(axis), buf)
            _, minimum, maximum, _, _, res = struct.unpack("6i", buf)
            if res > 0:
                return float(res)
            return max(1.0, (maximum - minimum) / 100.0)   # assume ~100 mm wide
        except OSError:
            return 10.0

    # --- toggle --------------------------------------------------------------

    def _grab(self, on):
        for fd in self.fds:
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 1 if on else 0)
            except OSError:
                pass

    def toggle(self):
        if not self.active and not self.conn.connected:
            print("\n· The iPhone isn't connected yet (see the instructions above).")
            return
        self.active = not self.active
        self._grab(self.active)
        if self.active:
            print("\n▶ Controlling the iPhone. RIGHT CTRL to switch back to the laptop.")
        else:
            self._release_all()
            print("\n■ Mouse and keyboard are back on the laptop.")

    def _release_all(self):
        self.mods, self.pressed, self.buttons = 0, [], 0
        self.conn.send(b"\xa1\x01" + bytes(8))
        self.conn.send(b"\xa1\x02" + bytes(4))

    # --- HID reports ---------------------------------------------------------

    def _keyboard_report(self):
        keys = (self.pressed + [0] * 6)[:6]
        self.conn.send(bytes([0xA1, 0x01, self.mods, 0] + keys))

    def _mouse_report(self, dx, dy, wheel):
        while True:
            px, py = max(-127, min(127, dx)), max(-127, min(127, dy))
            pw = max(-127, min(127, wheel))
            self.conn.send(struct.pack("<BBBbbb", 0xA1, 0x02, self.buttons, px, py, pw))
            dx, dy, wheel = dx - px, dy - py, wheel - pw
            if not (dx or dy or wheel):
                break

    def _move(self, mx, my, mwheel=0.0):
        self.rem_x += mx
        self.rem_y += my
        self.rem_wheel += mwheel
        ix, iy, iw = int(self.rem_x), int(self.rem_y), int(self.rem_wheel)
        self.rem_x -= ix
        self.rem_y -= iy
        self.rem_wheel -= iw
        self.pend_x += ix
        self.pend_y += iy
        self.pend_w += iw

    def _pending(self):
        return bool(self.pend_x or self.pend_y or self.pend_w)

    def _flush(self, force=False):
        """Send the accumulated movement, at most once every INTERVAL."""
        if not self._pending():
            return
        now = time.monotonic()
        if not force and now - self.last_sent < INTERVAL:
            return
        self.last_sent = now
        dx, dy, w = self.pend_x, self.pend_y, self.pend_w
        self.pend_x = self.pend_y = self.pend_w = 0
        self._mouse_report(dx, dy, w)

    def _buttons_changed(self):
        """Button changes go out immediately (together with any pending movement)."""
        if self._pending():
            self._flush(force=True)
        else:
            self._mouse_report(0, 0, 0)
        self.last_sent = time.monotonic()

    # --- events --------------------------------------------------------------

    def _key(self, code, value):
        if code == self.toggle_key:
            if value == 0:                  # on release, so the key doesn't get "stuck"
                self.toggle()
            return
        if not self.active or value == 2:   # 2 = autorepeat: the iPhone handles it
            return
        if code in MODIFIERS:
            bit = MODIFIERS[code]
            self.mods = (self.mods | bit) if value else (self.mods & ~bit)
        elif code in KEYS:
            hid = KEYS[code]
            if value and hid not in self.pressed:
                self.pressed.append(hid)
            elif not value and hid in self.pressed:
                self.pressed.remove(hid)
        else:
            return
        self._keyboard_report()

    def _button(self, code, value):
        bit = {BTN_LEFT: 1, BTN_RIGHT: 2, BTN_MIDDLE: 4}.get(code)
        if bit is None:
            return False
        self.buttons = (self.buttons | bit) if value else (self.buttons & ~bit)
        return True

    def _mouse_event(self, kind, code, value):
        if kind == EV_REL:
            if code == REL_X:
                self.dx += value
            elif code == REL_Y:
                self.dy += value
            elif code == REL_WHEEL:
                self.wheel += value * self.invert
        elif kind == EV_KEY:
            if self._button(code, value):
                self._buttons_changed()
        elif kind == EV_SYN:
            if self.dx or self.dy or self.wheel:
                self._move(self.dx * self.speed, self.dy * self.speed, self.wheel)
            self.dx = self.dy = self.wheel = 0

    def _touchpad_event(self, fd, kind, code, value):
        t = self.tp[fd]
        resx, resy = self.absinfo[fd]
        if kind == EV_KEY:
            if code == BTN_TOUCH:
                if value:
                    t.update(touching=True, x=None, y=None, start=time.monotonic(),
                             travel=0.0, max_fingers=max(1, t["fingers"]), clicked=False)
                else:
                    t["touching"] = False
                    duration = time.monotonic() - t["start"]
                    if not t["clicked"] and duration < 0.22 and t["travel"] < 2.0:
                        bit = 2 if t["max_fingers"] >= 2 else 1   # two-finger tap = right click
                        self.buttons |= bit
                        self._buttons_changed()
                        self.buttons &= ~bit
                        self._buttons_changed()
            elif code == BTN_TOOL_FINGER and value:
                t["fingers"] = 1
            elif code == BTN_TOOL_DOUBLETAP and value:
                t["fingers"] = 2
            elif code == BTN_TOOL_TRIPLETAP and value:
                t["fingers"] = 3
            elif code == BTN_TOOL_QUADTAP and value:
                t["fingers"] = 4
            elif code in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
                t["clicked"] = True
                if code == BTN_LEFT and value and t["fingers"] >= 2:
                    code = BTN_RIGHT
                if not value:
                    self.buttons = 0
                    self._buttons_changed()
                elif self._button(code, value):
                    self._buttons_changed()
            if t["touching"]:
                t["max_fingers"] = max(t["max_fingers"], t["fingers"])
        elif kind == EV_ABS:
            if code == ABS_X:
                t["nx"] = value
            elif code == ABS_Y:
                t["ny"] = value
        elif kind == EV_SYN and t["touching"]:
            nx, ny = t.get("nx"), t.get("ny")
            if nx is None or ny is None:
                return
            if t["x"] is not None:
                mmx, mmy = (nx - t["x"]) / resx, (ny - t["y"]) / resy
                t["travel"] += abs(mmx) + abs(mmy)
                if t["fingers"] >= 2 and not (self.buttons & 1):
                    self._move(0, 0, mmy * 0.6 * self.invert)   # two-finger scrolling
                else:
                    self._move(mmx * 5 * self.speed, mmy * 5 * self.speed)
            t["x"], t["y"] = nx, ny

    def loop(self):
        while True:
            timeout = INTERVAL if self._pending() else None
            ready, _, _ = select.select(list(self.fds), [], [], timeout)
            for fd in ready:
                try:
                    data = os.read(fd, EVENT.size * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    print(f"\n· {self.fds[fd][2]} was disconnected")
                    os.close(fd)
                    del self.fds[fd]
                    continue
                dev_kind = self.fds[fd][1]
                for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, kind, code, value = EVENT.unpack_from(data, i)
                    if dev_kind == "keyboard":
                        if kind == EV_KEY:
                            self._key(code, value)
                    elif self.active:
                        if dev_kind == "mouse":
                            self._mouse_event(kind, code, value)
                        else:
                            self._touchpad_event(fd, kind, code, value)
            if self.active:
                self._flush()
            if self.active and not self.conn.connected:
                self.active = False
                self._grab(False)
                print("■ Connection lost: mouse and keyboard are back on the laptop.")


# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(description="Use your laptop as a Bluetooth mouse and keyboard for your iPhone.")
    p.add_argument("action", nargs="?", default="start", choices=["start", "setup", "undo"])
    p.add_argument("--name", default=DEFAULT_NAME, help="Name the iPhone will see")
    p.add_argument("--speed", type=float, default=1.0, help="Pointer speed multiplier (e.g. 0.5 or 2)")
    p.add_argument("--key", type=int, default=KEY_RIGHTCTRL,
                   help="evdev code of the toggle key (default 97 = Right Ctrl; 70 = Scroll Lock)")
    p.add_argument("--invert-scroll", action="store_true", help="Invert scrolling direction")
    a = p.parse_args()

    if a.action == "setup":
        return setup()
    if a.action == "undo":
        return undo()

    require_root()
    if not (hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_L2CAP")):
        fail("This Python build has no Bluetooth socket support.")
    if not input_plugin_disabled():
        fail("Set up Bluetooth first (only once):\n"
             "    sudo python3 iphone_mouse.py setup")

    print("Laptop devices:")
    conn = HIDConnection()
    inp = Input(conn, a.speed, a.key, a.invert_scroll)
    inp.open(*find_devices())

    _refs = start_bluez(a.name, conn)
    threading.Thread(target=server, args=(conn,), daemon=True).start()
    threading.Thread(target=reconnect, args=(conn,), daemon=True).start()

    print(f"""
Ready. The laptop shows up over Bluetooth as "{a.name}".

First time only, on the iPhone:
  Settings → Accessibility → Touch → AssistiveTouch (turn it on)
  → Devices → Bluetooth Devices → "{a.name}"

Then press RIGHT CTRL to control the iPhone. Ctrl+C here to quit
(with control back on the laptop).""")
    try:
        inp.loop()
    except KeyboardInterrupt:
        inp._grab(False)
        print("\nBye.")


if __name__ == "__main__":
    main()
