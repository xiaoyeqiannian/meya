#!/bin/zsh

project_dir="${0:A:h}"
installed_app="$HOME/Library/Input Methods/麦芽 Meya.app"
if [[ -d "$installed_app" ]]; then
  open -n "$installed_app"
else
  open -n "$project_dir/麦芽 Meya.app"
fi
