# iPhone Mirror for Linux 🖱️📱

Control your **iPhone from a Linux laptop**, like macOS "iPhone Mirroring":
see the phone's screen on your laptop and use it with your mouse, touchpad and keyboard.

No jailbreak, no Mac and nothing to install on the iPhone.

Made by **PANKKY**.

## How it works

There are two parts:

1. **[UxPlay](https://github.com/FDH2/UxPlay)** turns your laptop into an AirPlay receiver, so you can see the iPhone's screen.
2. **`iphone_mouse.py`** (this project) makes the laptop show up on the iPhone as a **Bluetooth mouse and keyboard**, and forwards your laptop's mouse, touchpad and keyboard to it. The iPhone uses it through AssistiveTouch.

## Requirements

- Linux with BlueZ (tested on Fedora) and a laptop with Bluetooth
- Python 3 with `python3-dbus` and `python3-gobject` (Fedora usually ships them; on Debian/Ubuntu: `python3-dbus python3-gi`)
- iPhone and laptop on the same Wi-Fi network (5 GHz works best)

## Installation

### 1. UxPlay (to see the screen)

Fedora:

```bash
sudo dnf install uxplay gstreamer1-plugins-bad-free gstreamer1-plugin-openh264
sudo systemctl enable --now avahi-daemon
sudo firewall-cmd --permanent --add-service=mdns
sudo firewall-cmd --permanent --add-port=7000-7001/tcp --add-port=7100/tcp
sudo firewall-cmd --permanent --add-port=6000-6001/udp --add-port=7011/udp
sudo firewall-cmd --reload
```

Ubuntu/Debian: `sudo apt install uxplay` (and open the same ports if you use a firewall).

### 2. The mouse (to control it)

```bash
git clone https://github.com/P4NKK-TRYHARD/iPhone-Mirror-for-Linux.git
cd iPhone-Mirror-for-Linux
sudo python3 iphone_mouse.py setup
```

You only need to run `setup` once. It disables the `input` plugin of `bluetoothd`, which is required so the laptop can act as a mouse.

> ⚠️ While it's set up this way, **Bluetooth** mice or keyboards used by the laptop itself won't work (USB ones are fine).
> To go back to normal: `sudo python3 iphone_mouse.py undo`

## Usage

1. In one terminal, start UxPlay:
   ```bash
   uxplay -p
   ```
   On the iPhone: **Control Center → Screen Mirroring → UxPlay@…**
2. In another terminal, start the mouse:
   ```bash
   sudo python3 iphone_mouse.py
   ```
3. **The first time**, on the iPhone:
   **Settings → Accessibility → Touch → AssistiveTouch** (turn it on) → **Devices → Bluetooth Devices → "Linux Mouse"**
4. When the terminal says **"✓ iPhone connected"**, press **Right Ctrl** to hand control to the iPhone. Press it again to get it back on the laptop.

### Controls

| On the laptop | On the iPhone |
|---|---|
| Move the mouse or touchpad | Move the pointer |
| Click, or tap the touchpad | Tap |
| Two-finger tap / right click | Secondary click |
| Two fingers on the touchpad / wheel | Scroll |
| Keyboard | Type (set the layout in *Settings → General → Keyboard → Hardware Keyboard*) |
| Super + H | Home screen |
| Super + Space | Search |

### Options

```
--speed 0.5        Slower pointer (or 2 for faster)
--key 70           Change the toggle key (evdev code; 70 = Scroll Lock)
--invert-scroll    Invert scrolling direction
--name "My PC"     Name the iPhone will see
```

To quit, first switch control back to the laptop with Right Ctrl, then press **Ctrl+C**.

## Tip: keep the iPhone's screen "off"

Locking the iPhone stops AirPlay, but you can make its screen nearly black while the laptop still shows it normally:
- Set brightness to the minimum.
- Turn on *Settings → Accessibility → Display & Text Size → Reduce White Point* at 100%.
- Add it to the *Accessibility Shortcut* so a triple-click of the side button toggles it.
- Set *Auto-Lock* to "Never" while you use it.

## Troubleshooting

- **The iPhone doesn't see "Linux Mouse"**: make sure the laptop's Bluetooth is on and that you ran `setup`.
- **AirPlay keeps dropping**: Wi-Fi and Bluetooth often share the same antenna. Use a 5 GHz Wi-Fi network.
- **The pointer doesn't move**: AssistiveTouch must be **on**.
- **It feels laggy**: look at the iPhone itself to check whether the lag comes from the mouse or from the AirPlay picture.

## License

MIT © 2026 **PANKKY**. You're free to use, modify and share it, as long as you keep the copyright notice and credit the author.
