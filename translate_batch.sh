#!/usr/bin/env bash
# 스트라이크 더 블러드 전권 순차 번역 스크립트
set -euo pipefail

INDIR="/mnt/d/nov/스더블"
OUTDIR="output/스더블"
PYTHON=".venv/bin/python"

declare -a FILES=(
    "三雲-岳斗_-ストライク・ザ・ブラッド-APPEND1-人形師の遺産-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド-APPEND2-彩昂祭の昼と夜-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド9-黒の剣巫-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド10-冥き神王の花嫁-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド11-逃亡の第四真祖-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド12-咎神の騎士-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド13-タルタロスの薔薇-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド14-黄金の日々-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド15-真祖大戦-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド16-陽炎の聖騎士-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド17-折れた聖槍-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド18-真説・ヴァルキュリアの王国-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド19-終わらない夜の宴-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド20-再会の吸血姫-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド21-十二眷獣と血の従者たち-_電撃文庫_.txt"
    "三雲-岳斗_-ストライク・ザ・ブラッド22-暁の凱旋-_電撃文庫_.txt"
)

declare -a OUTNAMES=(
    "APPEND1_ko.txt"
    "APPEND2_ko.txt"
    "09_ko.txt"
    "10_ko.txt"
    "11_ko.txt"
    "12_ko.txt"
    "13_ko.txt"
    "14_ko.txt"
    "15_ko.txt"
    "16_ko.txt"
    "17_ko.txt"
    "18_ko.txt"
    "19_ko.txt"
    "20_ko.txt"
    "21_ko.txt"
    "22_ko.txt"
)

TOTAL=${#FILES[@]}

for i in "${!FILES[@]}"; do
    N=$((i + 1))
    INFILE="$INDIR/${FILES[$i]}"
    OUTFILE="$OUTDIR/${OUTNAMES[$i]}"

    echo ""
    echo "========================================"
    echo "[$N/$TOTAL] ${OUTNAMES[$i]}"
    echo "  입력: $INFILE"
    echo "  출력: $OUTFILE"
    echo "========================================"

    $PYTHON translate.py \
        --input  "$INFILE" \
        --output "$OUTFILE"

    echo "[$N/$TOTAL] 완료: $OUTFILE"
done

echo ""
echo "전체 번역 완료"
