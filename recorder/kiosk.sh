#!/bin/sh
# GEA Recorder - the monitor on the box: a screen on the Pi's HDMI port that shows the live view (http://127.0.0.1:8780/live)
# full-screen from boot, with nothing to press. Run once, as root, after install.sh:   sudo sh kiosk.sh
# What it does on Raspberry Pi OS (Bookworm or later, the desktop image): installs Chromium if it is missing, sets the Pi
# to sign the desktop in at boot without a password prompt, turns screen blanking off, and writes the desktop's autostart
# so Chromium opens the live view in kiosk mode (no bars, no buttons) when the desktop comes up. Both Wayland compositors
# the Pi uses (labwc on Pi 5, wayfire on Pi 4) and X are covered. Undo: delete the autostart lines it added.
set -e
USER_NAME=${SUDO_USER:-pi}
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
URL=http://127.0.0.1:8780/live
CMD="chromium-browser --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --check-for-update-interval=31536000 --app=$URL"
command -v chromium-browser >/dev/null 2>&1 || command -v chromium >/dev/null 2>&1 || apt-get install -y chromium-browser >/dev/null 2>&1 || apt-get install -y chromium >/dev/null 2>&1 || true
command -v chromium-browser >/dev/null 2>&1 || CMD=$(echo "$CMD" | sed 's/^chromium-browser/chromium/')
if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_boot_behaviour B4 || true        # desktop, signed in at boot
  raspi-config nonint do_blanking 1 || true               # screen blanking off
fi
# labwc (Pi 5 default)
mkdir -p "$HOME_DIR/.config/labwc"
grep -q "gea-recorder kiosk" "$HOME_DIR/.config/labwc/autostart" 2>/dev/null || printf '%s\n' "# gea-recorder kiosk" "$CMD &" >> "$HOME_DIR/.config/labwc/autostart"
# wayfire (Pi 4 default)
mkdir -p "$HOME_DIR/.config"
if ! grep -q "gea-recorder-kiosk" "$HOME_DIR/.config/wayfire.ini" 2>/dev/null; then
  printf '\n[autostart]\ngea-recorder-kiosk = %s\n' "$CMD" >> "$HOME_DIR/.config/wayfire.ini"
fi
# X (older images)
mkdir -p "$HOME_DIR/.config/lxsession/LXDE-pi"
grep -q "gea-recorder kiosk" "$HOME_DIR/.config/lxsession/LXDE-pi/autostart" 2>/dev/null || printf '%s\n' "# gea-recorder kiosk" "@xset s off" "@xset -dpms" "@$CMD" >> "$HOME_DIR/.config/lxsession/LXDE-pi/autostart"
chown -R "$USER_NAME":"$USER_NAME" "$HOME_DIR/.config"
echo "== kiosk set for $USER_NAME: the live view opens full-screen at boot. Reboot to see it. The same view is at $URL from any browser on the pad network."
