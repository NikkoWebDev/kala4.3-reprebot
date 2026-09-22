"""Evalúa el RAG local: 1 punto por keywords presentes + fuente esperada.
Preguntas must_dont_know: 1 punto solo si responde No lo sé."""
import json
import sys
import urllib.request

RAG = "http://127.0.0.1:8000/api/search"


def ask(q, k):
    body = json.dumps({"question": q, "k": k}).encode()
    req = urllib.request.Request(
        RAG, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def main():
    spec = json.load(open("/home/admin/EVAL_QUESTIONS.json"))
    total = 0
    got = 0
    lines = []
    for it in spec:
        total += 1
        try:
            res = ask(it["q"], it.get("k", 5))
            ans = res.get("answer", "")
            srcs = " ".join(s["doc_name"] for s in res.get("sources", []))
            if it.get("must_dont_know"):
                ok = ans.strip().startswith("No lo sé")
            else:
                ok_kw = all(kw.lower() in ans.lower()
                            for kw in it.get("keywords", []))
                ok_doc = (not it.get("docs") or
                          any(d.lower() in srcs.lower()
                              for d in it["docs"]))
                ok = ok_kw and ok_doc
        except Exception as e:  # noqa: BLE001
            ok = False
            ans = f"ERROR: {e}"
        got += ok
        lines.append(f"{'OK ' if ok else 'FAIL'} | {it['q'][:60]} | {ans[:90]}")
    pct = 100.0 * got / total if total else 0
    print(f"EVAL {got}/{total} = {pct:.0f}%")
    print("\n".join(lines))
    return pct


if __name__ == "__main__":
    main()
