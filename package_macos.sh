#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h}"
app_dir="$project_dir/麦芽 Meya.app"
package_root="$project_dir/.package-root"
output_dir="$project_dir/dist"
scripts_dir="$project_dir/installer"
helper="$project_dir/.build/register-input-method"

if [[ ! -d "$app_dir" ]]; then
  "$project_dir/build_app.sh"
fi
if [[ ! -x "$helper" ]]; then
  /usr/bin/xcrun swiftc \
    "$project_dir/app/RegisterInputMethod.swift" \
    -o "$helper" \
    -module-cache-path "$project_dir/.cache/swift-app-modules" \
    -framework Carbon
fi

version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$app_dir/Contents/Info.plist")"
package_path="$output_dir/麦芽-Meya-v${version}-本地语音输入法-离线安装包.pkg"
payload_dir="$package_root/Library/Application Support/LocalVoiceInput/InstallerPayload"

if [[ "$package_root" != "$project_dir/.package-root" ]]; then
  echo "安装包临时目录校验失败" >&2
  exit 1
fi
/bin/rm -rf "$package_root"
/bin/mkdir -p "$payload_dir" "$output_dir"
/usr/bin/ditto "$app_dir" "$payload_dir/Meya.app.payload"
/bin/cp "$helper" "$payload_dir/register-input-method"
/bin/chmod +x "$payload_dir/register-input-method"
/bin/cp "$scripts_dir/com.dp.inputmethod.LocalVoiceInput.agent.plist" "$payload_dir/com.dp.inputmethod.LocalVoiceInput.agent.plist"
/bin/chmod +x "$scripts_dir/postinstall"

/usr/bin/pkgbuild \
  --root "$package_root" \
  --scripts "$scripts_dir" \
  --identifier "com.dp.inputmethod.LocalVoiceInput.installer" \
  --version "$version" \
  --install-location / \
  "$package_path"

/usr/sbin/pkgutil --check-signature "$package_path" || true
/usr/bin/codesign --verify --deep --strict "$app_dir"
/bin/rm -rf "$package_root"

echo "已生成: $package_path"
echo "安装后键盘使用系统简体拼音/ABC，长按 Fn 启动麦芽 Meya，临时文字在输入框内显示。"
