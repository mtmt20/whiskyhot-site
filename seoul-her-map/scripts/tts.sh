#!/usr/bin/env bash
# narration.txt → narration.mp3 (무료 Edge TTS, 중국어 여성 목소리). 설치: pip install edge-tts
# 사용: scripts/tts.sh out/<note>
set -e
DIR="${1:?usage: scripts/tts.sh out/<note>}"
VOICE="${VOICE:-zh-CN-XiaoxiaoNeural}"
command -v edge-tts >/dev/null || { echo "edge-tts 가 없습니다: pip install edge-tts"; exit 1; }
edge-tts --voice "$VOICE" --rate=-5% -f "$DIR/narration.txt" --write-media "$DIR/narration.mp3"
echo "✔ $DIR/narration.mp3  (剪映에 넣고 cards/*.png 를 3초씩 배치하면 영상 노트)"
