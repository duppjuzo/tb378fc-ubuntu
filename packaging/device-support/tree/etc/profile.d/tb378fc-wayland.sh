# shellcheck shell=sh
# SPDX-License-Identifier: GPL-2.0-only
# Ensure interactive login shells inherit Wayland defaults for Electron/Chromium/Firefox/Qt/SDL.
export ELECTRON_OZONE_PLATFORM_HINT="${ELECTRON_OZONE_PLATFORM_HINT:-auto}"
export OZONE_PLATFORM_HINT="${OZONE_PLATFORM_HINT:-auto}"
export NIXOS_OZONE_WL="${NIXOS_OZONE_WL:-1}"
export MOZ_ENABLE_WAYLAND="${MOZ_ENABLE_WAYLAND:-1}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-wayland,x11}"
