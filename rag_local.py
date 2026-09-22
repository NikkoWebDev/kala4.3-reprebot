"""RAG local: Qwen 2.5-3B (llama-server) + TF-IDF sobre DATA_RAG.

POST /api/search {"question": str, "k": int=3}
-> {"answer": str, "sources": [{"doc_name": str, "score": float}]}
"""
import json
import os
import re
import unicodedata
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

DATA_DIR = os.environ.get("DATA_DIR", "/home/admin/DATA_RAG")
LLAMA_URL = os.environ.get(
    "LLAMA_URL", "http://127.0.0.1:8080/v1/chat/completions")
PORT = int(os.environ.get("RAG_PORT", "8000"))
MIN_SCORE = float(os.environ.get("MIN_SCORE", "0.12"))
CHUNK = int(os.environ.get("CHUNK", "900"))
OVERLAP = int(os.environ.get("OVERLAP", "150"))

SYSTEM = (
    "Eres un asistente de normativa universitaria. REGLAS ESTRICTAS:\n"
    "1. Responde de forma MUY concisa (máximo 5 líneas).\n"
    "2. Responde SOLO con información de los documentos dados. "
    "Si no está en los documentos, di exactamente: "
    "\"No lo sé según los documentos disponibles.\"\n"
    "3. Cita siempre la fuente entre corchetes con el nombre del documento, "
    "ej: [Resolución 037 de 2010 Rectoría].\n"
    "4. No inventes artículos, números ni fechas.\n"
    "5. Incluye siempre los códigos, números, artículos y porcentajes "
    "exactos que aparezcan en los documentos.\n"
    "6. Empieza la respuesta con el dato concreto (código, número o "
    "artículo) y luego explica en una línea.\n"
    "7. Sintetiza TODOS los fragmentos recuperados, no solo el primero."
)

TOKEN_RE = re.compile(r"[a-záéíóúñü0-9]+")


def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def tokens(s):
    return TOKEN_RE.findall(s.lower())


def load_chunks():
    chunks = []  # (doc_name, filename, text)
    for fn in sorted(os.listdir(DATA_DIR)):
        if not (fn.endswith(".txt") or fn.endswith(".md")):
            continue
        doc = fn.split("__", 1)[-1].replace(
            "-", " ").replace(".txt", "").replace(".md", "").title()
        with open(os.path.join(DATA_DIR, fn), encoding="utf-8",
                  errors="ignore") as f:
            text = re.sub(r"\s+", " ", f.read()).strip()
        for i in range(0, max(len(text) - OVERLAP, 1), CHUNK - OVERLAP):
            c = text[i:i + CHUNK]
            if len(c.strip()) > 100:
                chunks.append((doc, fn, c))
    return chunks


print("Cargando documentos...", flush=True)
CHUNKS = load_chunks()
print(f"{len(CHUNKS)} fragmentos de "
      f"{len(set(d for d, _, _ in CHUNKS))} documentos.", flush=True)

print("Ajustando TF-IDF...", flush=True)
from sklearn.feature_extraction.text import TfidfVectorizer
VEC = TfidfVectorizer(preprocessor=norm,
                      token_pattern=r"(?u)[a-z0-9]+",
                      ngram_range=(1, 2), sublinear_tf=True)
MAT = VEC.fit_transform([t for _, _, t in CHUNKS])
print("Índice listo.", flush=True)

DOCS = sorted(set(d for d, _, _ in CHUNKS))
DOC_TOKENS = [set(re.findall(r"[a-z0-9]+", norm(d))) for d, _, _ in CHUNKS]
CHUNK_TOKENS = [set(re.findall(r"[a-z0-9]+", norm(t))) for _, _, t in CHUNKS]
CURATED = {
    "34245__acuerdo-033-de-2007-csu-lineamientos-formacion.txt",
    "34983__acuerdo-008-de-2008-csu-estatuto-estudiantil-academico.txt",
    "35443__acuerdo-070-de-2009-consejo-academico.txt",
    "36920__resolucion-037-de-2010-rectoria.txt",
    "37192__acuerdo-044-de-2009-csu-estatuto-bienestar-y-convivencia.txt",
    "46769__acuerdo-036-de-2012-csu.txt",
    "47025__acuerdo-026-de-2012-consejo-academico.txt",
    "56987__acuerdo-102-de-2013-csu.txt",
    "66330__acuerdo-089-de-2014-consejo-academico-posgrados.txt",
    "69337__acuerdo-155-de-2014-csu.txt",
    "95482__resolucion-13-de-2020-vicerrectoria-academica.txt",
    "00000__compendio-normativo-ingenieria-unal-bogota.md",
    "00001__directorio-normativo-ingenierias-bogota-posgrados.md",
}
META_RE = re.compile(
    r"(lista.*document|que documentos|cuales documentos|"
    r"de que tienes informacion|que informacion tienes|"
    r"que sabes|sobre que puedes responder|"
    r"que temas manejas|tus fuentes)")


def catalog():
    ans = (f"Tengo {len(DOCS)} documentos de normativa UNAL: "
           + "; ".join(f"[{d}]" for d in DOCS) + ".")
    src = [{"doc_name": d, "score": 1.0} for d in DOCS]
    return ans, src


def retrieve(question, k=3):
    q = VEC.transform([question])
    scores = (MAT @ q.T).toarray().ravel().astype(float)
    qtok = set(re.findall(r"[a-z0-9]+", norm(question)))
    qtok.discard("en")
    qtok.discard("de")
    qtok.discard("la")
    qtok.discard("el")
    for i, dt in enumerate(DOC_TOKENS):
        shared = len(qtok & dt)
        if shared:
            scores[i] += min(0.05 * shared, 0.15)
    if qtok:
        for i, ct in enumerate(CHUNK_TOKENS):
            cov = len(qtok & ct) / len(qtok)
            scores[i] += 0.25 * cov
    for i, (_, fn, _) in enumerate(CHUNKS):
        if fn in CURATED:
            scores[i] += 0.12
    top = sorted(range(len(CHUNKS)), key=lambda i: -scores[i])[:k]
    return [(CHUNKS[i][0], CHUNKS[i][2], float(scores[i]))
            for i in top if scores[i] > 0]


def ask_llama(question, ctx):
    context = "\n\n".join(f"[{d}]\n{t}" for d, t, _ in ctx)
    msgs = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",
         "content": f"Documentos:\n{context}\n\nPregunta: {question}"},
    ]
    body = json.dumps({"messages": msgs, "temperature": 0.0,
                       "max_tokens": 220}).encode()
    req = urllib.request.Request(LLAMA_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read())
    return out["choices"][0]["message"]["content"].strip()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path != "/api/search":
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            q = json.loads(self.rfile.read(n) or b"{}")
            question = q.get("question", "").strip()
            k = int(q.get("k", 3))
            if not question:
                raise ValueError("pregunta vacía")
            if META_RE.search(norm(question)):
                ans, src = catalog()
            else:
                ctx = retrieve(question, k)
                if not ctx or ctx[0][2] < MIN_SCORE:
                    ans = "No lo sé según los documentos disponibles."
                    src = []
                else:
                    ans = ask_llama(question, ctx)
                    src = [{"doc_name": d, "score": round(s, 4)}
                           for d, _, s in ctx]
            res = json.dumps({"answer": ans, "sources": src},
                             ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)
        except Exception as e:  # noqa: BLE001
            res = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)


HTTPServer(("0.0.0.0", PORT), H).serve_forever()
