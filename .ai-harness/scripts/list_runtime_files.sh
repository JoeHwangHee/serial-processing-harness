#!/usr/bin/env bash
# list_runtime_files.sh — 버킷 A(런타임 풋프린트) 파일 목록을 HROOT 기준 상대경로로 출력.
#
# 이식(transplant.sh)과 런타임 레포 발행(publish-runtime.sh)이 공유하는
# **버킷 A 정의의 단일 진실원천**. 두 소비자가 같은 목록을 쓰므로 드리프트가 없다.
#
# 포함: .ai-harness/ · .claude/ · .codex/ · AGENTS.md
# 제외: scripts/bench/compare/(제3자 비교 도구) · *-workspace/(스킬 개발 워크스페이스)
#       · evals/(스킬 회귀 픽스처) · skills/*-dev/(dev 전용 스킬, 예: 배포) · Codex 크레덴셜
set -euo pipefail
HROOT="${1:-$PWD}"; HROOT="$(cd -- "$HROOT" && pwd)"
cd "$HROOT"
for d in .ai-harness .claude .codex; do
  [ -d "$d" ] || continue
  find "$d" -type f \
    -not -path '*.ai-harness/scripts/bench/compare/*' \
    -not -path '*-workspace/*' \
    -not -path '*/evals/*' \
    -not -path '*/skills/*-dev/*' \
    -not -path '*.codex/auth.json' \
    -not -path '*.codex/credentials.json' \
    -not -path '*.codex/*.key' \
    -not -path '*.codex/*.pem'
done
if [ -f AGENTS.md ]; then printf '%s\n' AGENTS.md; fi
