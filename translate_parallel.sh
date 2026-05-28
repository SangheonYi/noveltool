#!/usr/bin/env bash
# 스트라이크 더 블러드 병렬 번역 (최대 10개 동시)
# 이미 완료된 파일은 건너뜀

set -uo pipefail

INDIR="/mnt/d/nov/스더블"
OUTDIR="output/스더블"
PYTHON=".venv/bin/python"
MAX_JOBS=10

declare -a FILES=(
    "三雲-岳斗_-ストライク・ザ・ブラッド-APPEND1-人形師の遺産-_電撃文庫_.txt|APPEND1_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド-APPEND2-彩昂祭の昼と夜-_電撃文庫_.txt|APPEND2_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド9-黒の剣巫-_電撃文庫_.txt|09_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド10-冥き神王の花嫁-_電撃文庫_.txt|10_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド11-逃亡の第四真祖-_電撃文庫_.txt|11_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド12-咎神の騎士-_電撃文庫_.txt|12_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド13-タルタロスの薔薇-_電撃文庫_.txt|13_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド14-黄金の日々-_電撃文庫_.txt|14_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド15-真祖大戦-_電撃文庫_.txt|15_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド16-陽炎の聖騎士-_電撃文庫_.txt|16_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド17-折れた聖槍-_電撃文庫_.txt|17_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド18-真説・ヴァルキュリアの王国-_電撃文庫_.txt|18_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド19-終わらない夜の宴-_電撃文庫_.txt|19_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド20-再会の吸血姫-_電撃文庫_.txt|20_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド21-十二眷獣と血の従者たち-_電撃文庫_.txt|21_ko.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド22-暁の凱旋-_電撃文庫_.txt|22_ko.txt"
)

# 이미 실행 중인 translate.py PID를 초기 목록에 포함 (총 동시 실행 수 제한 준수)
mapfile -t RUNNING < <(pgrep -f "translate.py --input" 2>/dev/null || true)
PIDS=("${RUNNING[@]}")
[ ${#PIDS[@]} -gt 0 ] && echo "[감지] 이미 실행 중인 번역 프로세스: ${#PIDS[@]}개 (PID: ${PIDS[*]})"

# 실행 중인 job 수 갱신
update_pids() {
    local alive=()
    for pid in "${PIDS[@]}"; do
        kill -0 "$pid" 2>/dev/null && alive+=("$pid")
    done
    PIDS=("${alive[@]}")
}

# 빈 슬롯 생길 때까지 대기
wait_for_slot() {
    while true; do
        update_pids
        [ ${#PIDS[@]} -lt $MAX_JOBS ] && return
        sleep 3
    done
}

TOTAL=${#FILES[@]}
STARTED=0
SKIPPED=0

for entry in "${FILES[@]}"; do
    FNAME="${entry%%|*}"
    OUTNAME="${entry##*|}"
    INFILE="$INDIR/$FNAME"
    OUTFILE="$OUTDIR/$OUTNAME"

    # 이미 완료된 파일 건너뜀 (출력 라인 수 >= 입력의 99%)
    if [ -f "$OUTFILE" ]; then
        in_lines=$(wc -l < "$INFILE")
        out_lines=$(wc -l < "$OUTFILE")
        threshold=$(( in_lines * 99 / 100 ))
        if [ "$out_lines" -ge "$threshold" ]; then
            echo "[SKIP] $OUTNAME (완료: $out_lines/$in_lines줄)"
            ((SKIPPED++)) || true
            continue
        fi
        # 부분 완료: state 파일 있으면 이어쓰기, 없으면 처음부터 (translate.py 내부 처리)
        echo "[RESUME] $OUTNAME ($out_lines/$in_lines줄 완료, 이어쓰기)"
    fi

    wait_for_slot

    echo "[START] $OUTNAME (현재 실행 중: ${#PIDS[@]}개)"
    $PYTHON translate.py --input "$INFILE" --output "$OUTFILE" \
        >> "output/batch_parallel.log" 2>&1 &
    PIDS+=($!)
    ((STARTED++)) || true
done

echo "[대기] 모든 번역 완료 대기 중..."
wait
echo "[완료] 전체 번역 완료 (시작: $STARTED, 건너뜀: $SKIPPED)"
