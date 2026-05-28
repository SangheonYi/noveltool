#!/usr/bin/env bash
cd /home/sayi/ft/noveltool

INDIR="/mnt/d/nov/스더블"
declare -A INFILES
INFILES[APPEND1]="三雲-岳斗_-ストライク・ザ・ブラッド-APPEND1-人形師の遺産-_電撃文庫_.txt"
INFILES[APPEND2]="三雲-岳斗_-ストライク・ザ・ブラッド-APPEND2-彩昂祭の昼と夜-_電撃文庫_.txt"
INFILES[09]="三雲-岳斗_-ストライク・ザ・ブラッド9-黒の剣巫-_電撃文庫_.txt"
INFILES[10]="三雲-岳斗_-ストライク・ザ・ブラッド10-冥き神王の花嫁-_電撃文庫_.txt"
INFILES[11]="三雲-岳斗_-ストライク・ザ・ブラッド11-逃亡の第四真祖-_電撃文庫_.txt"
INFILES[12]="三雲-岳斗_-ストライク・ザ・ブラッド12-咎神の騎士-_電撃文庫_.txt"
INFILES[13]="三雲-岳斗_-ストライク・ザ・ブラッド13-タルタロスの薔薇-_電撃文庫_.txt"
INFILES[14]="三雲-岳斗_-ストライク・ザ・ブラッド14-黄金の日々-_電撃文庫_.txt"
INFILES[15]="三雲-岳斗_-ストライク・ザ・ブラッド15-真祖大戦-_電撃文庫_.txt"
INFILES[16]="三雲-岳斗_-ストライク・ザ・ブラッド16-陽炎の聖騎士-_電撃文庫_.txt"
INFILES[17]="三雲-岳斗_-ストライク・ザ・ブラッド17-折れた聖槍-_電撃文庫_.txt"
INFILES[18]="三雲-岳斗_-ストライク・ザ・ブラッド18-真説・ヴァルキュリアの王国-_電撃文庫_.txt"
INFILES[19]="三雲-岳斗_-ストライク・ザ・ブラッド19-終わらない夜の宴-_電撃文庫_.txt"
INFILES[20]="三雲-岳斗_-ストライク・ザ・ブラッド20-再会の吸血姫-_電撃文庫_.txt"
INFILES[21]="三雲-岳斗_-ストライク・ザ・ブラッド21-十二眷獣と血の従者たち-_電撃文庫_.txt"
INFILES[22]="三雲-岳斗_-ストライク・ザ・ブラッド22-暁の凱旋-_電撃文庫_.txt"

keys=(APPEND1 APPEND2 09 10 11 12 13 14 15 16 17 18 19 20 21 22)

DONE=0; RUNNING=0; WAIT=0
printf "%-10s %6s %6s %6s  %-20s  %s\n" "파일" "완료" "전체" "진행률" "진행바" "상태"
printf -- "----------------------------------------------------------------------\n"
for i in "${!keys[@]}"; do
  key="${keys[$i]}"
  total=$(wc -l < "$INDIR/${INFILES[$key]}")
  f="output/스더블/${key}_ko.txt"
  if [ ! -f "$f" ]; then
    printf "%-10s %6s %6d %6s  %-20s  ⏳ 대기중\n" "$key" "-" "$total" "-" "░░░░░░░░░░░░░░░░░░░░"
    ((WAIT++)); continue
  fi
  d=$(wc -l < "$f")
  pct=$(awk "BEGIN{v=int($d/$total*100); print (v>100)?100:v}")
  bar=$(awk "BEGIN{n=int($pct/5); s=\"\"; for(i=0;i<n;i++) s=s\"█\"; for(i=n;i<20;i++) s=s\"░\"; print s}")
  if ps aux | grep -q "[t]ranslate.py.*${key}_ko"; then
    st="🔄 번역중"; ((RUNNING++))
  else
    st="✅ 완료"; ((DONE++))
  fi
  printf "%-10s %6d %6d %5d%%  %s  %s\n" "$key" "$d" "$total" "$pct" "$bar" "$st"
done
printf -- "----------------------------------------------------------------------\n"
printf "✅ 완료: %d   🔄 번역중: %d   ⏳ 대기: %d   /   전체: 16\n" "$DONE" "$RUNNING" "$WAIT"
