#!/bin/bash
# Turno nocturno: investiga normativa UNAL -> amplía DATA_RAG -> reindexa -> evalúa.
# Corre hasta que exista /home/admin/STOP_NIGHT (o `systemctl stop night`).
set -u
HOME_DIR=/home/admin
NEW_DIR=$HOME_DIR/DATA_RAG_NEW
WORK=$HOME_DIR/work
mkdir -p "$NEW_DIR" "$WORK"
export PATH="$HOME_DIR/.opencode/bin:$PATH"
CYCLE=0
while [ ! -f $HOME_DIR/STOP_NIGHT ]; do
  CYCLE=$((CYCLE+1))
  N_DOCS=$(ls $HOME_DIR/DATA_RAG/*.txt 2>/dev/null | wc -l)
  N_NEW=$(ls $NEW_DIR/*.txt 2>/dev/null | wc -l)
  LAST_EVAL=$(tail -1 $HOME_DIR/PROGRESS.md 2>/dev/null | grep -o 'EVAL [0-9]*/[0-9]* = [0-9]*%' || echo "sin eval")
  echo "=== ciclo $CYCLE $(date -u) docs=$N_DOCS nuevos=$N_NEW last=$LAST_EVAL ===" >> $HOME_DIR/PROGRESS.md
  cat > $WORK/cycle_prompt.txt <<PROMPT
Eres un investigador de normativa de la Universidad Nacional de Colombia (UNAL).
Ya existen $N_DOCS documentos en /home/admin/DATA_RAG/ (mira sus nombres primero con ls, NO los re-descargues).
Último eval: $LAST_EVAL.

TAREA DE ESTE CICLO (una sola cosa, bien hecha):
1. Busca en https://legal.unal.edu.co (usa curl) normativa NUEVA no presente aún, con prioridad:
   a) Facultad de Ingeniería sede Bogotá, planes de estudio y créditos (especialmente Ingeniería de Sistemas y Computación),
   b) Acuerdos del CSU y Consejo Académico sobre pregrado, admisiones, nivelación, calificaciones.
2. De cada norma nueva encontrada descarga el texto, límpialo (solo texto plano, sin HTML/menús) y guárdalo en /home/admin/DATA_RAG_NEW/ con nombre <id>__<slug>.txt (ej: 37000__acuerdo-012-de-2011-csu.txt). Mínimo 500 caracteres por archivo.
3. Si no encuentras nada nuevo este ciclo, responde exactamente SIN_NADA_NUEVO y explica qué buscaste.
Al final lista los archivos que creaste (o SIN_NADA_NUEVO).
PROMPT
  timeout 1500 opencode run "$(cat $WORK/cycle_prompt.txt)" 2>&1 | tail -30 >> $HOME_DIR/PROGRESS.md
  ADDED=0
  for f in $NEW_DIR/*.txt; do
    [ -f "$f" ] || continue
    b=$(basename "$f")
    if [ ! -f "$HOME_DIR/DATA_RAG/$b" ] && [ $(wc -c <"$f") -gt 500 ]; then
      cp "$f" "$HOME_DIR/DATA_RAG/$b" && rm "$f" && ADDED=$((ADDED+1))
    else
      rm -f "$f"
    fi
  done
  if [ "$ADDED" -gt 0 ]; then
    sudo systemctl restart rag 2>/dev/null || systemctl --user restart rag 2>/dev/null || (pkill -f rag_local.py; sleep 2; nohup $HOME_DIR/finetune/venv/bin/python $HOME_DIR/rag_local.py >>$HOME_DIR/rag.log 2>&1 &)
    sleep 15
  fi
  echo "--- agregados=$ADDED ---" >> $HOME_DIR/PROGRESS.md
  $HOME_DIR/finetune/venv/bin/python $HOME_DIR/eval_rag.py 2>&1 | head -15 >> $HOME_DIR/PROGRESS.md
  sleep 60
done
echo "STOP $(date -u)" >> $HOME_DIR/PROGRESS.md
