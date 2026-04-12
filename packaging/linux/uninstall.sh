#!/bin/sh
set -eu

PREFIX="${PREFIX:-$HOME/.local}"
APP_NAME="MacroTouch"
APP_DIR="$PREFIX/opt/$APP_NAME"
BIN_LINK="$PREFIX/bin/macrotouch"
DESKTOP_PATH="$PREFIX/share/applications/macrotouch.desktop"
ICON_PATH="$PREFIX/share/icons/hicolor/256x256/apps/macrotouch.png"

rm -rf "$APP_DIR"
rm -f "$BIN_LINK"
rm -f "$DESKTOP_PATH"
rm -f "$ICON_PATH"

echo "MacroTouch removed from $PREFIX"
