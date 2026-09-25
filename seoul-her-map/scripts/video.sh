#!/usr/bin/env bash
# cards/*.png + narration.mp3 → note.mp4 (1080x1440, 카드당 3초, 나레이션 있으면 길이를 맞춤)
# 사용: scripts/video.sh out/<note>   (시스템 ffmpeg 없으면 playwright 동봉 ffmpeg 사용)
set -e
DIR="${1:?usage: scripts/video.sh out/<note>}"
FF="$(command -v ffmpeg || ls /opt/pw-browsers/ffmpeg-*/ffmpeg-linux 2>/dev/null | head -1)"
[ -x "$FF" ] || { echo "ffmpeg 를 찾을 수 없습니다"; exit 1; }
SEC="${SEC:-3}"
TMP="$DIR/.frames"; rm -rf "$TMP"; mkdir -p "$TMP"
i=0; for f in "$DIR"/cards/*.png; do i=$((i+1)); cp "$f" "$TMP/$(printf 'f%03d.png' $i)"; done
if [ -f "$DIR/narration.mp3" ]; then
  DUR=$("$FF" -i "$DIR/narration.mp3" 2>&1 | grep -oP 'Duration: \K[0-9:.]+' | awk -F: '{print $1*3600+$2*60+$3}')
  SEC=$(python3 -c "print(round(max(2.5, $DUR/$i), 2))")
fi
AUDIO=(); [ -f "$DIR/narration.mp3" ] && AUDIO=(-i "$DIR/narration.mp3" -shortest -c:a aac)
"$FF" -y -loglevel error -framerate "1/$SEC" -i "$TMP/f%03d.png" "${AUDIO[@]}" \
  -vf "scale=1080:1440" -r 30 -c:v libx264 -pix_fmt yuv420p "$DIR/note.mp4"
rm -rf "$TMP"; echo "✔ $DIR/note.mp4 (카드 ${i}장, 카드당 ${SEC}s)"
