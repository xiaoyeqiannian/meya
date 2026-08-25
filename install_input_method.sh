#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h}"
source_app="$project_dir/麦芽 Meya.app"
target_dir="$HOME/Library/Input Methods"
target_app="$target_dir/麦芽 Meya.app"
legacy_app="$target_dir/LocalVoiceInput.app"
maya_app="$target_dir/MAYA.app"
agent_dir="$HOME/Library/LaunchAgents"
agent="$agent_dir/com.dp.inputmethod.LocalVoiceInput.agent.plist"
agent_label="com.dp.inputmethod.LocalVoiceInput.agent"
backup_root="$HOME/Library/Application Support/Meya/app-backups"
tcc_marker="$HOME/Library/Application Support/Meya/tcc-identity-v2"

if [[ ! -d "$source_app" ]]; then
  "$project_dir/build_app.sh"
fi
if [[ ! -e "$project_dir/models" && -d "${project_dir:h}/local-asr-demo/models" ]]; then
  ln -s "${project_dir:h}/local-asr-demo/models" "$project_dir/models"
fi
if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
  echo "本地识别环境缺失，正在安装 .venv …"
  "$project_dir/bootstrap.sh"
fi

launchctl bootout "gui/$(id -u)/$agent_label" >/dev/null 2>&1 || true
killall LocalVoiceInput >/dev/null 2>&1 || true
killall MAYA >/dev/null 2>&1 || true
killall Meya >/dev/null 2>&1 || true
mkdir -p "$target_dir" "$backup_root"
# 旧副本留在 Input Methods 里会让「隐私与安全性」出现多个麦芽，勾选对不上当前进程。
find "$target_dir" -maxdepth 1 \( -name '*.backup' -o -name '*.app.backup' \) -exec mv {} "$backup_root/" \;
if [[ -d "$target_app" ]]; then
  timestamp="$(date '+%Y%m%d-%H%M%S')"
  mv "$target_app" "$backup_root/麦芽-Meya-preinstall-$timestamp.app.backup"
fi
if [[ -d "$maya_app" ]]; then
  timestamp="$(date '+%Y%m%d-%H%M%S')"
  mv "$maya_app" "$backup_root/MAYA-pre-Meya-$timestamp.app.backup"
fi
if [[ -d "$legacy_app" ]]; then
  timestamp="$(date '+%Y%m%d-%H%M%S')"
  mv "$legacy_app" "$backup_root/LocalVoiceInput-pre-MAYA-$timestamp.app.backup"
fi
ditto "$source_app" "$target_app"
codesign --verify --deep --strict "$target_app"
"$project_dir/.build/register-input-method" "$target_app" --voice-only

mkdir -p "$agent_dir"
cp "$project_dir/installer/com.dp.inputmethod.LocalVoiceInput.agent.plist" "$agent"
plutil -replace ProgramArguments -json "[\"$target_app/Contents/MacOS/Meya\"]" "$agent"
chmod 600 "$agent"
launchctl bootstrap "gui/$(id -u)" "$agent"
if [[ ! -f "$tcc_marker" ]]; then
  mkdir -p "$(dirname "$tcc_marker")"
  date > "$tcc_marker"
fi
launchctl kickstart -k "gui/$(id -u)/$agent_label"

sleep 1
permission_status="$project_dir/runtime/fn-status.json"
accessibility="false"
input_monitoring="false"
if [[ -f "$permission_status" ]]; then
  accessibility="$(plutil -extract accessibility raw "$permission_status" 2>/dev/null || echo false)"
  input_monitoring="$(plutil -extract inputMonitoring raw "$permission_status" 2>/dev/null || echo false)"
fi

echo
echo "安装位置: $target_app"
echo "个人词库与纠错表在 ~/Library/Application Support/Meya/，重装不会覆盖。"
echo "麦芽 Meya 已安装；键盘继续使用 macOS 简体拼音/ABC，长按 Fn 使用本地语音。"
if [[ "$accessibility" == "true" && "$input_monitoring" == "true" ]]; then
  echo "现有系统权限已保留，无需重新授权。"
else
  echo "首次安装尚缺少系统权限时，再到「输入监控」和「辅助功能」中启用麦芽 Meya。"
fi
