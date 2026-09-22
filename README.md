# RAG UNAL en AWS — Qwen 2.5-3B local

Proyecto de asistente de normativa UNAL corriendo 100% en una EC2 Debian. Responde conciso, cita fuentes, dice no se cuando no sabe. Nada de humo.

## La maquina

- EC2 `54.204.249.29` (us-east-1), Debian 13 trixie
- 2 vCPU Xeon Platinum 8488C, 7.6 GB RAM, 16 GB disco
- Acceso: `ssh -i nikkoclaves.pem admin@ec2-54-204-249-29.compute-1.amazonaws.com`
- Ojo: a sept 2026 el security group quedo mal editado y el SSH esta caido. Hay que reabrir 22/80/443 en consola AWS antes de tocar nada.

## El modelo

Qwen 2.5-3B-Instruct en `Q4_K_M` (2 GB), de `Qwen/Qwen2.5-3B-Instruct-GGUF`. Lo baje con wget directo al home. Corre con llama.cpp compilado desde fuente (`ggml-org/llama.cpp`, `GGML_NATIVE=ON`, `-j2`), sirviendo en `llama-server :8080`.

Sobre el "finetune": no hay finetune de pesos y no lo va a haber en esta maquina. El `llama-finetune` pide modelo FP32 y el README de llama.cpp habla de 24 GB de RAM para 1B. Aca hay 7.6. Intentarlo es quemar tiempo. El caso es que para responder normativa no se necesita: lo que da precision es retrieval bueno sobre corpus bueno. Todo el trabajo fue por ese lado.

## RAG local (`rag_local.py`)

Python stdlib + scikit-learn, sin frameworks. Hace esto:

1. Carga `DATA_RAG/` (`.txt` y `.md`), trocea a 900 chars con overlap 150.
2. TF-IDF 1-2 gramas sobre los fragmentos.
3. `POST /api/search {"question", "k"}` → top-k → system prompt estricto → `llama-server /v1/chat/completions` → `{answer, sources}`.

Mejoras que si movieron la aguja, en orden:

- Normalizacion de tildes con unicodedata. Sin esto, "nivelacion" no matcheaba "nivelación". Basico y me costo un par de pruebas darme cuenta.
- `MIN_SCORE=0.12`: si el mejor match es ruido, responde "No lo se segun los documentos disponibles" con sources vacio. Sin llamar al modelo.
- Bonus por nombre de documento (+0.15 cap). "posgrados consejo academico 2014" matchea el filename del Acuerdo 089.
- Bonus por cobertura (+0.25): el fragmento que contiene todos los terminos de la pregunta sube.
- Boost a curados (+0.12): los 11 originales + 2 guias pesan mas que los 251 del scraping nocturno. Suena a trampa, pero los curados los revise yo. Funciona.
- Temperatura 0.0. Respuestas deterministicas. Con temp > 0 el eval bailaba entre corridas.
- Prompt: conciso, solo documentos, citar siempre, no inventar numeros, empezar con el dato concreto, sintetizar TODOS los fragmentos.

Preguntas meta ("lista tus documentos", "de que tienes informacion?") devuelven el catalogo sin pasar por el modelo.

## Eval

`EVAL_QUESTIONS.json` + `eval_rag.py`: 10 preguntas, 1 punto si trae keywords + fuente esperada, el "no se" solo vale fuera de dominio. Progresion real: 70% → 80% → 90% → **10/10**. El 100% es sobre mi set, no es garantia universal. Si encuentran una pregunta que falle, se suma al set y se itera. Asi se mantiene.

## Turno nocturno (Hermes + OpenCode + Big Pickle)

Instale Hermes Agent v0.21.3 en el server. Detalle importante: la key de OpenCode Zen da 403 fuera de OpenCode (el tier gratis solo sirve dentro del CLI), asi que el flujo real es Hermes delegando a `opencode run` con el modelo `big-pickle`, configurado como provider custom en `~/.config/opencode/opencode.json`. Verificado con `PICKLE_OK`.

El `night_shift.sh` (servicio systemd `night`, hoy detenido) hacia ciclos: Big Pickle investigaba legal.unal.edu.co, guardaba .txt limpios en `DATA_RAG_NEW/`, el supervisor fusionaba, reiniciaba `rag` y corria el eval. Resultado: corpus de 11 → 264 documentos, 3.0 MB. Parado con `STOP_NIGHT` + `systemctl stop night` cuando el cuello dejo de ser corpus y paso a ser retrieval.

Las guias de Gemini (`.docx` en `Proyectos/aws/`) las pase a `.md` livianos y entraron al corpus como curados: `00000__compendio-...` (PAPA, estatutos, Sistemas: 003-A/2022, 006/2023, 026/2014) y `00001__directorio-...` (carreras, posgrados, bienestar, movilidad). El tercer doc trae el catalogo de PDFs oficiales (PEP Sistemas, mallas) para futuras descargas.

## Servicios en el server

| Servicio | Que es | Puerto |
|---|---|---|
| `llama` | llama-server Qwen 3B | 8080 local |
| `rag` | rag_local.py | 8000 local |
| `night` | turno nocturno (detenido) | — |
| nginx | reverse proxy `apifn.nikko.dev` | 80/443 |

Todos con `Restart=always`. Sobreviven reinicios.

## API publica (pendiente SG)

DNS listo: `apifn.nikko.dev → 54.204.249.29`. Nginx configurado y certbot instalado. Falta: abrir 80/443 en el security group, luego `certbot --nginx -d apifn.nikko.dev`. Uso objetivo:

```bash
curl -X POST https://apifn.nikko.dev/api/search \
  -H 'Content-Type: application/json' \
  -d '{"question":"qué es el PAPA","k":5}'
```

Mientras tanto, por tunel SSH:

```bash
ssh -i nikkoclaves.pem -L 8000:127.0.0.1:8000 -N admin@ec2-54-204-249-29.compute-1.amazonaws.com
curl -X POST http://127.0.0.1:8000/api/search -H 'Content-Type: application/json' -d '{"question":"qué es el PAPA","k":5}'
```

## Reprebot (las capacidades reales)

El monorepo `Proyectos/IA/Reprebot` es donde vive todo lo que el usuario toca. Mi RAG de AWS es el motor local; Reprebot es el producto.

- `apps/backend` (FastAPI): `POST /api/chat` y `/api/chat/stream` (SSE), `POST /api/search`, `GET/POST /api/documents`, `POST /api/forum/answer` (publica en foro CEIS via Supabase, con api key), `POST /api/whatsapp/answer`, `POST /v1/chat/completions` (compatible OpenAI), `GET /api/health`. RAG con embeddings Jina + Groq `gpt-oss-120b` para lo general. Rate limit 20/min.
- `apps/frontend` + `Proyectos/ChatIA` (KalaChat, Astro 5 + Tailwind, Markdown + SSE streaming, localStorage): el chat propio.
- `apps/whatsapp` (Node + Baileys): escucha un grupo, responde cuando lo etiquetan. Numero secundario siempre, Baileys no es oficial y banean.
- `apps/scraper`: crawler de legal.unal.edu.co con manejo de captcha. De ahi salio parte del corpus.

## Archivos de este directorio

`rag_local.py` (servicio), `EVAL_QUESTIONS.json` + `eval_rag.py` (harness), `night_shift.sh` (turno), `00000/00001*.md` (guias curadas), `DATA_RAG/` (11 originales), `raw_*.txt` (extracciones crudas de los docx). `nikkoclaves.pem` tiene permiso 400, no subirla a ningun repo.

Pendiente: reabrir el SG, emitir el TLS, y reactivar el turno si el eval cae debajo de 10/10 con preguntas nuevas.
