#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOTNET="${DOTNET:-dotnet}"
RID="${MEYA_MAC_RID:-osx-arm64}"
TEST_PROJECT="$ROOT/tests/dotnet/Meya.Core.ContractTests/Meya.Core.ContractTests.csproj"
DESKTOP_PROJECT="$ROOT/src/dotnet/Meya.Desktop/Meya.Desktop.csproj"
PUBLISH="$ROOT/dist/Meya.Desktop.$RID"
APP="$ROOT/dist/Meya Desktop.app"

"$DOTNET" build "$TEST_PROJECT" -c Release
"$DOTNET" "$ROOT/tests/dotnet/Meya.Core.ContractTests/bin/Release/net8.0/Meya.Core.ContractTests.dll" "$ROOT"
"$DOTNET" publish "$DESKTOP_PROJECT" -c Release -r "$RID" --self-contained true \
  -p:PublishSingleFile=false -p:PublishTrimmed=false -o "$PUBLISH"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp -R "$PUBLISH/." "$APP/Contents/MacOS/"
chmod +x "$APP/Contents/MacOS/Meya.Desktop"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Meya Desktop</string>
  <key>CFBundleDisplayName</key><string>麦芽 Meya</string>
  <key>CFBundleIdentifier</key><string>com.meya.shared-desktop.preview</string>
  <key>CFBundleExecutable</key><string>Meya.Desktop</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP"
codesign --verify --strict "$APP"

if [[ "${MEYA_SKIP_UI_SMOKE:-0}" != "1" ]]; then
  "$APP/Contents/MacOS/Meya.Desktop" --overlay-smoke
fi

echo "Shared macOS app: $APP"
