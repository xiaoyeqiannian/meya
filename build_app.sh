#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h}"
build_dir="$project_dir/.build"
app_dir="$project_dir/麦芽 Meya.app"
module_cache="$project_dir/.cache/swift-app-modules"

mkdir -p "$build_dir" "$module_cache"

if [[ -d "$app_dir" ]]; then
  rm -rf "$app_dir"
fi

xcrun swiftc \
  "$project_dir/app/MeyaFramedProtocol.swift" \
  "$project_dir/app/LocalVoiceInput.swift" \
  -o "$build_dir/Meya" \
  -parse-as-library \
  -module-cache-path "$module_cache" \
  -framework Cocoa \
  -framework AVFoundation \
  -framework Carbon \
  -framework ApplicationServices \
  -framework InputMethodKit

xcrun swiftc \
  "$project_dir/app/RegisterInputMethod.swift" \
  -o "$build_dir/register-input-method" \
  -module-cache-path "$module_cache" \
  -framework Carbon

xcrun swiftc \
  "$project_dir/app/GenerateIcon.swift" \
  -o "$build_dir/generate-icon" \
  -module-cache-path "$module_cache" \
  -framework AppKit

xcrun swiftc \
  "$project_dir/app/PackIcns.swift" \
  -o "$build_dir/pack-icns" \
  -module-cache-path "$module_cache"

mkdir -p "$app_dir/Contents/MacOS" "$app_dir/Contents/Helpers" "$app_dir/Contents/Resources"
cp "$build_dir/Meya" "$app_dir/Contents/MacOS/Meya"
cp "$build_dir/register-input-method" "$app_dir/Contents/Helpers/register-input-method"
cp "$project_dir/app/Info.plist" "$app_dir/Contents/Info.plist"
/usr/bin/plutil -replace LocalVoiceProjectPath -string "$project_dir" "$app_dir/Contents/Info.plist"
"$build_dir/generate-icon" "$app_dir/Contents/Resources/MeyaInputSource-v3.pdf" 512 app "$project_dir/app/MayaMascot3D.png"
"$build_dir/generate-icon" "$app_dir/Contents/Resources/MeyaStatus-v5.pdf" 128 template
cp "$project_dir/app/MayaMascot3D.png" "$app_dir/Contents/Resources/MayaMascot3D.png"

iconset_dir="$build_dir/Meya.iconset"
rm -rf "$iconset_dir"
mkdir -p "$iconset_dir"
for spec in \
  "16 icon_16x16.png" \
  "32 icon_16x16@2x.png" \
  "32 icon_32x32.png" \
  "64 icon_32x32@2x.png" \
  "128 icon_128x128.png" \
  "256 icon_128x128@2x.png" \
  "256 icon_256x256.png" \
  "512 icon_256x256@2x.png" \
  "512 icon_512x512.png" \
  "1024 icon_512x512@2x.png"; do
  size="${spec%% *}"
  name="${spec#* }"
  "$build_dir/generate-icon" "$iconset_dir/$name" "$size" app "$project_dir/app/MayaMascot3D.png"
done
"$build_dir/pack-icns" "$iconset_dir" "$app_dir/Contents/Resources/Meya-v3.icns"
mkdir -p "$app_dir/Contents/Resources/zh_CN.lproj"
cp "$project_dir/app/zh_CN.lproj/InfoPlist.strings" "$app_dir/Contents/Resources/zh_CN.lproj/InfoPlist.strings"
chmod +x "$app_dir/Contents/MacOS/Meya"
chmod +x "$app_dir/Contents/Helpers/register-input-method"
# 不用 hardened runtime，也不用默认的 anchor apple generic。
# 否则 tccd 会把授权绑在某次构建的签名上，重装后设置页仍显示已勾选，
# 进程却一直 Failed to match existing code requirement。
local_signing_keychain_v2="$HOME/Library/Keychains/MeyaSigningV2.keychain-db"
legacy_signing_keychain="$HOME/Library/Keychains/MeyaLocalSigning.keychain-db"
codesign_keychain=()
certificate_keychain=()
codesign_identity=""
if [[ -f "$local_signing_keychain_v2" ]]; then
  security unlock-keychain -p '' "$local_signing_keychain_v2"
  codesign_identity="$(security find-identity -v -p codesigning "$local_signing_keychain_v2" | awk '/Meya Local Code Signing V2/{print $2; exit}')"
  if [[ -n "$codesign_identity" ]]; then
    codesign_keychain=(--keychain "$local_signing_keychain_v2")
    certificate_keychain=("$local_signing_keychain_v2")
  fi
fi
if [[ -z "$codesign_identity" ]]; then
  codesign_identity="$(security find-identity -v -p codesigning | awk '/Apple Development/{print $2; exit}')"
fi
if [[ -z "$codesign_identity" && -f "$legacy_signing_keychain" ]]; then
  codesign_identity="$(security find-identity -v -p codesigning "$legacy_signing_keychain" | awk '/Meya Local Code Signing/{print $2; exit}')"
  if [[ -n "$codesign_identity" ]]; then
    codesign_keychain=(--keychain "$legacy_signing_keychain")
    certificate_keychain=("$legacy_signing_keychain")
  fi
fi
signed_req_file="$build_dir/meya-signed.req"
adhoc_req_file="$build_dir/meya-adhoc.req"
printf '%s\n' 'designated => identifier "com.dp.inputmethod.LocalVoiceInput"' > "$adhoc_req_file"
sign_nested() {
  local identity="$1"
  codesign "${codesign_keychain[@]}" --force --sign "$identity" "$app_dir/Contents/Helpers/register-input-method"
}

if [[ -n "$codesign_identity" ]]; then
  if ! sign_nested "$codesign_identity"; then
    echo "签名身份存在但不可用；已停止构建，避免临时签名导致麦芽权限失效。" >&2
    exit 1
  fi
  # Derive the signing OU from the selected certificate at build time.
  # This preserves the stable TCC requirement without publishing a maintainer
  # identifier in the repository. Self-signed certificates fall back to their
  # public certificate hash.
  identity_upper="$(printf '%s' "$codesign_identity" | tr '[:lower:]' '[:upper:]')"
  certificate_alias="$(security find-certificate -a -Z "${certificate_keychain[@]}" \
    | awk -v wanted="$identity_upper" '
      $0 == "SHA-1 hash: " wanted { found=1; next }
      found && /"alis"<blob>=/ {
        line=$0
        sub(/^.*"alis"<blob>="/, "", line)
        sub(/".*$/, "", line)
        print line
        exit
      }
    ')"
  certificate_ou=""
  if [[ -n "$certificate_alias" ]]; then
    certificate_ou="$(security find-certificate -c "$certificate_alias" -p \
      "${certificate_keychain[@]}" \
      | openssl x509 -noout -subject -nameopt RFC2253 2>/dev/null \
      | awk -F'OU=' 'NF > 1 {split($2, value, ","); print value[1]; exit}')"
  fi
  if [[ -n "$certificate_ou" && ${#certificate_ou} -le 64 ]]; then
    printf 'designated => identifier "com.dp.inputmethod.LocalVoiceInput" and certificate leaf[subject.OU] = "%s"\n' \
      "$certificate_ou" > "$signed_req_file"
  else
    printf 'designated => identifier "com.dp.inputmethod.LocalVoiceInput" and certificate leaf = H"%s"\n' \
      "$codesign_identity" > "$signed_req_file"
  fi
  if ! codesign --force \
      "${codesign_keychain[@]}" \
      --entitlements "$project_dir/app/LocalVoiceInput.entitlements" \
      --requirements "$signed_req_file" \
      --sign "$codesign_identity" "$app_dir"; then
    echo "签名身份存在但不可用；已停止构建，避免临时签名导致麦芽权限失效。" >&2
    exit 1
  fi
else
  sign_nested "-"
  codesign --force \
    --entitlements "$project_dir/app/LocalVoiceInput.entitlements" \
    --requirements "$adhoc_req_file" \
    --sign - "$app_dir"
fi
codesign --verify --deep --strict "$app_dir"
echo "签名要求: $(codesign -d -r- "$app_dir" 2>&1 | awk '/designated/{print}')"

echo "已构建: $app_dir"
