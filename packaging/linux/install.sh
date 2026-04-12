#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PAYLOAD_DIR="$SCRIPT_DIR/payload"
PREFIX="${PREFIX:-$HOME/.local}"
APP_NAME="MacroTouch"
APP_DIR="$PREFIX/opt/$APP_NAME"
BIN_DIR="$PREFIX/bin"
APPS_DIR="$PREFIX/share/applications"
ICON_DIR="$PREFIX/share/icons/hicolor/256x256/apps"
ICON_PATH="$ICON_DIR/macrotouch.png"
DESKTOP_PATH="$APPS_DIR/macrotouch.desktop"

if [ ! -d "$PAYLOAD_DIR/app" ]; then
  echo "Missing payload/app directory." >&2
  exit 1
fi

mkdir -p "$APP_DIR" "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
rm -rf "$APP_DIR"
cp -R "$PAYLOAD_DIR/app/." "$APP_DIR/"

if [ -f "$PAYLOAD_DIR/macrotouch.png" ]; then
  install -m 0644 "$PAYLOAD_DIR/macrotouch.png" "$ICON_PATH"
fi

if [ -f "$PAYLOAD_DIR/macrotouch.desktop.in" ]; then
  sed \
    -e "s|__APP_EXEC__|$APP_DIR/MacroTouch|g" \
    -e "s|__APP_ICON__|$ICON_PATH|g" \
    "$PAYLOAD_DIR/macrotouch.desktop.in" > "$DESKTOP_PATH"
  chmod 0644 "$DESKTOP_PATH"
fi

ln -sfn "$APP_DIR/MacroTouch" "$BIN_DIR/macrotouch"

echo "MacroTouch installed to $APP_DIR"
echo "Launcher: $BIN_DIR/macrotouch"
echo "Desktop entry: $DESKTOP_PATH"
