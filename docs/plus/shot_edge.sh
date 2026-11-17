#!/bin/bash
# shot.sh in.html out.png "hash" -> headless Edge screenshot (Windows side, via a temp copy)
EDGE="/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
W=$(wslpath -w /mnt/c/Windows/Temp)
cp "$1" /mnt/c/Windows/Temp/iv_shot.html
rm -f /mnt/c/Windows/Temp/iv_shot.png
timeout 120 "$EDGE" --headless=new --disable-gpu --hide-scrollbars --window-size=1600,1000 --virtual-time-budget=8000 \
  --user-data-dir="$W\\iv_edge" --screenshot="$W\\iv_shot.png" "file:///C:/Windows/Temp/iv_shot.html#$3" >/dev/null 2>&1
cp /mnt/c/Windows/Temp/iv_shot.png "$2"
