#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h}"
duration="${1:-10}"
timestamp="$(date +%Y%m%d-%H%M%S)"
audio_path="$project_dir/recordings/$timestamp.wav"

if [[ ! -x "$project_dir/recorder" || "$project_dir/Recorder.swift" -nt "$project_dir/recorder" ]]; then
  echo "首次运行：正在编译 macOS 录音程序…"
  mkdir -p "$project_dir/.cache/swift-modules"
  xcrun swiftc "$project_dir/Recorder.swift" \
    -o "$project_dir/recorder" \
    -module-cache-path "$project_dir/.cache/swift-modules" \
    -framework AVFoundation
fi

"$project_dir/recorder" "$audio_path" "$duration"
"$project_dir/.venv/bin/python" "$project_dir/transcribe.py" "$audio_path" --offline
