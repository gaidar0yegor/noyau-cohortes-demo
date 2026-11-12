#!/usr/bin/env python3
"""Une seule H100 : la vraie capacité en sessions de 8 k, et l'extraction documentaire.

Ce que ce banc doit remplacer dans la note : la ligne « 52 sessions de 8 k (13 par carte) », qui est un
calcul KV, et la phrase d'introduction qui en dépend. Deux mesures, dans le même conteneur, sur la même
charge :

  1. échelle de concurrence 8 / 13 / 17 / 24, chaque requête avec un contexte de 8 k **unique**
     (préfixes différents, sinon le cache de préfixe fausse tout) et 96 tokens de sortie ;
  2. extraction : 200 comptes rendus synthétiques -> JSON contraint par schéma, débit en documents/heure.

Rien de nouveau n'est mis au point ici : même image, mêmes drapeaux que les campagnes précédentes.

Usage : env -u PYTHONPATH modal run bench_ladder.py
"""
from __future__ import annotations

import json
import random
import time

from pathlib import Path

import modal

IMAGE = (
    modal.Image.from_registry("vllm/vllm-openai:latest", add_python="3.12")
    .pip_install("aiohttp>=3.9")
    .entrypoint([])
)

app = modal.App("adlin-ladder-1xh100")

MOE = "Qwen/Qwen3-30B-A3B-Instruct-2507"
MAXLEN = 16384
UTIL = "0.90"
SLO_ITL_P95 = 100.0
SLO_TTFT_P95 = 2000.0
PORT = 8000


def _pct(values, p):
    if not values:
        return None
    v = sorted(values)
    return v[max(0, min(len(v) - 1, int(round((p / 100) * (len(v) - 1)))))]


def _contexte_unique(graine: int, tokens_cibles: int = 8000) -> str:
    """Contexte d'environ 8 k tokens, différent d'une requête à l'autre.

    Sans cette unicité, le cache de préfixe rendrait les mesures flatteuses : toutes les requêtes
    partageraient les mêmes tokens déjà en mémoire.
    """
    rnd = random.Random(graine)
    phrases = [
        "La patiente a été admise au service de chirurgie ambulatoire pour bilan.",
        "Le compte rendu mentionne une chimiothérapie néo-adjuvante de six cycles.",
        "L'anatomopathologie confirme un carcinome infiltrant avec récepteurs positifs.",
        "La réunion de concertation pluridisciplinaire propose une surveillance rapprochée.",
        "Le suivi biologique montre une numération stable sur les trois derniers mois.",
        "L'imagerie de contrôle ne retrouve pas de lésion secondaire visible.",
    ]
    parts = [f"DOSSIER-{graine}-{rnd.randrange(10**9)}"]
    budget = tokens_cibles * 4
    while len(" ".join(parts)) < budget:
        parts.append(rnd.choice(phrases))
        if rnd.random() < 0.05:
            parts.append(f"réf {rnd.randrange(10**6)}")
    return " ".join(parts)


async def _une_requete(session, base, contexte, max_tokens=96):
    """TTFT, ITL et erreurs d'une requête en streaming."""
    import aiohttp

    corps = {
        "model": "m",
        "messages": [{"role": "user",
                      "content": contexte + "\n\nQuestion : combien de dossiers sont concernés ?"}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
        "ignore_eos": True,
    }
    t0 = time.perf_counter()
    ttft = None
    instants = []
    n_tokens = 0
    try:
        async with session.post(f"{base}/v1/chat/completions", json=corps) as r:
            if r.status != 200:
                return {"ok": False, "erreur": f"HTTP {r.status}"}
            async for ligne in r.content:
                ligne = ligne.decode("utf-8", "ignore").strip()
                if not ligne.startswith("data:"):
                    continue
                data = ligne[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except Exception:                                # noqa: BLE001
                    continue
                if delta.get("content"):
                    maintenant = time.perf_counter()
                    if ttft is None:
                        ttft = (maintenant - t0) * 1000
                    else:
                        instants.append((maintenant - t0) * 1000)
                    n_tokens += 1
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "erreur": type(e).__name__}
    if ttft is None or n_tokens < 2:
        return {"ok": False, "erreur": "aucun token"}
    itl = [(b - a) for a, b in zip(instants, instants[1:])]
    return {"ok": True, "ttft": ttft, "itl": itl, "n": n_tokens,
            "total": (time.perf_counter() - t0)}


async def _palier(base, concurrence, graine_base, max_tokens=96):
    import asyncio
    import aiohttp

    contexte = _contexte_unique(graine_base)
    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        t0 = time.perf_counter()
        res = await asyncio.gather(*[
            _une_requete(session, base, contexte + f" [req {i}]", max_tokens)
            for i in range(concurrence)
        ])
        duree = time.perf_counter() - t0
    ok = [r for r in res if r.get("ok")]
    row = {"concurrence": concurrence, "erreurs": [r.get("erreur") for r in res if not r.get("ok")]}
    if not ok:
        row["note"] = "aucune requête réussie"
        return row
    itl = [x for r in ok for x in r["itl"]]
    ttft = [r["ttft"] for r in ok]
    row.update({
        "ttft_p50_ms": round(_pct(ttft, 50), 1),
        "ttft_p95_ms": round(_pct(ttft, 95), 1),
        "itl_p50_ms": round(_pct(itl, 50), 2),
        "itl_p95_ms": round(_pct(itl, 95), 2),
        "aggregate_tok_s": round(sum(r["n"] for r in ok) / duree, 1),
        "requests_ok": len(ok),
    })
    row["under_slo"] = bool(row["itl_p95_ms"] < SLO_ITL_P95 and row["ttft_p95_ms"] < SLO_TTFT_P95)
    return row


SCHEMA = {
    "type": "object",
    "properties": {
        "patient": {"type": "string", "pattern": "^P[0-9]{4}$"},
        "code_cim10": {"type": "string", "pattern": "^C50\\.[0-9]$"},
        "anapath": {"type": "boolean"},
        "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
    },
    "required": ["patient", "code_cim10", "anapath", "date"],
}


def _rapport(i: int) -> str:
    rnd = random.Random(1000 + i)
    return (f"Compte rendu d'anatomopathologie n°{i}. Patiente P{i:04d}, prélèvement du "
            f"2026-{rnd.randrange(1, 13):02d}-{rnd.randrange(1, 28):02d}. Diagnostic : carcinome "
            "infiltrant du sein, code C50.4. Validation anatomopathologique : "
            f"{'obtenue' if rnd.random() < 0.7 else 'en attente'}. Conduite à tenir discutée en RCP.")


async def _extraction(base, documents=200, concurrence=8):
    import asyncio
    import aiohttp

    async def un(i, session):
        corps = {
            "model": "m",
            "messages": [{"role": "user", "content":
                          "Extrais les champs au format JSON.\n\n" + _rapport(i)}],
            "max_tokens": 120, "temperature": 0,
            "extra_body": {"guided_json": SCHEMA},
        }
        t0 = time.perf_counter()
        try:
            async with session.post(f"{base}/v1/chat/completions", json=corps) as r:
                d = await r.json()
        except Exception as e:                                   # noqa: BLE001
            return {"ok": False, "err": type(e).__name__}
        try:
            obj = json.loads(d["choices"][0]["message"]["content"])
            valide = all(k in obj for k in SCHEMA["required"])
        except Exception:                                        # noqa: BLE001
            valide = False
        return {"ok": valide, "s": time.perf_counter() - t0}

    sem = asyncio.Semaphore(concurrence)
    timeout = aiohttp.ClientTimeout(total=900)

    async def garde(i, session):
        async with sem:
            return await un(i, session)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        t0 = time.perf_counter()
        res = await asyncio.gather(*[garde(i, session) for i in range(documents)])
        duree = time.perf_counter() - t0
    valides = sum(1 for r in res if r.get("ok"))
    return {"documents": documents, "valides": valides,
            "secondes": round(duree, 1),
            "documents_par_heure": round(3600 * documents / duree, 1),
            "documents_valides_par_heure": round(3600 * valides / duree, 1),
            "concurrence": concurrence}


@app.function(image=IMAGE, gpu="H100", timeout=2700, memory=32768)
def campagne() -> dict:
    """Une H100, mêmes drapeaux que les campagnes 4×H100 (max_model_len 16384, util 0.90)."""
    import asyncio
    import subprocess

    import aiohttp

    gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                         capture_output=True, text=True).stdout.strip()
    proc = subprocess.Popen(
        ["vllm", "serve", MOE, "--port", str(PORT), "--host", "0.0.0.0",
         "--tensor-parallel-size", "1", "--max-model-len", str(MAXLEN),
         "--gpu-memory-utilization", UTIL, "--disable-log-requests",
         "--enable-prefix-caching", "--served-model-name", "m"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{PORT}"
    t0 = time.time()
    try:
        while time.time() - t0 < 2400:
            try:
                async def _ping():
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
                        async with s.get(f"{base}/v1/models") as r:
                            return r.status == 200
                if asyncio.run(_ping()):
                    break
            except Exception:                                    # noqa: BLE001
                pass
            time.sleep(5)
        else:
            return {"erreur": "serveur non prêt", "gpu": gpu}
        pret = time.time() - t0

        out = {"gpu": gpu, "model": MOE, "max_model_len": MAXLEN, "ready_seconds": round(pret, 1),
               "slo": {"itl_p95_ms": SLO_ITL_P95, "ttft_p95_ms": SLO_TTFT_P95}, "paliers": []}
        for i, c in enumerate([8, 13, 17, 24]):
            out["paliers"].append(asyncio.run(_palier(base, c, graine_base=100 * (i + 1))))
        out["extraction"] = asyncio.run(_extraction(base))
        return out
    finally:
        proc.kill()


@app.local_entrypoint()
def main():
    t0 = time.time()
    res = campagne.remote()
    duree = time.time() - t0
    with open(str(Path(__file__).with_name("ladder.raw")), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("RESULT " + json.dumps(res, ensure_ascii=False))
    print(f"DUREE_TOTALE_S {duree:.0f} | COUT_ESTIME_USD {duree/3600*3.95:.2f}")
