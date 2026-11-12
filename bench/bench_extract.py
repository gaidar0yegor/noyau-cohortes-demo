#!/usr/bin/env python3
"""Extraction documentaire : 200 comptes rendus -> JSON contraint, sur une H100.

Le premier essai avait rendu 0 sortie conforme : j'envoyais la contrainte dans `extra_body`, qui n'existe
que dans le client Python, pas dans l'API HTTP. Ici, le script essaie les deux formes acceptées par le
serveur (`response_format` et `guided_json`) sur les premiers documents, garde celle qui produit du JSON
valide, puis mesure.

Usage : env -u PYTHONPATH modal run bench_extract.py
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

app = modal.App("adlin-extract-1xh100")
MOE = "Qwen/Qwen3-30B-A3B-Instruct-2507"
MAXLEN = 16384
UTIL = "0.90"
PORT = 8000

SCHEMA = {
    "type": "object",
    "properties": {
        "patient": {"type": "string", "pattern": "^P[0-9]{4}$"},
        "code_cim10": {"type": "string", "pattern": "^C50\\.[0-9]$"},
        "anapath": {"type": "boolean"},
        "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
    },
    "required": ["patient", "code_cim10", "anapath", "date"],
    "additionalProperties": False,
}


def _rapport(i: int) -> str:
    rnd = random.Random(1000 + i)
    return (f"Compte rendu d'anatomopathologie n°{i}. Patiente P{i:04d}, prélèvement du "
            f"2026-{rnd.randrange(1, 13):02d}-{rnd.randrange(1, 28):02d}. Diagnostic : carcinome "
            "infiltrant du sein, code C50.4. Validation anatomopathologique : "
            f"{'obtenue' if rnd.random() < 0.7 else 'en attente'}. Conduite à tenir discutée en RCP.")


PROMPT = ("Extrais les champs du compte rendu au format JSON : patient (identifiant P####), code_cim10, "
          "anapath (booléen), date (AAAA-MM-JJ).\n\n")


def _corps(i: int, mode: str) -> dict:
    corps = {"model": "m", "temperature": 0, "max_tokens": 150,
             "messages": [{"role": "user", "content": PROMPT + _rapport(i)}]}
    if mode == "response_format":
        corps["response_format"] = {"type": "json_schema",
                                    "json_schema": {"name": "extraction", "schema": SCHEMA}}
    elif mode == "guided_json":
        corps["guided_json"] = SCHEMA
    return corps


async def _un(session, base, i, mode):
    import aiohttp
    t0 = time.perf_counter()
    try:
        async with session.post(f"{base}/v1/chat/completions", json=_corps(i, mode)) as r:
            if r.status != 200:
                return {"ok": False, "err": f"HTTP {r.status}", "brut": (await r.text())[:200]}
            d = await r.json()
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "err": type(e).__name__}
    txt = d["choices"][0]["message"]["content"]
    try:
        obj = json.loads(txt)
        ok = all(k in obj for k in SCHEMA["required"])
    except Exception:                                            # noqa: BLE001
        ok, obj = False, None
    return {"ok": ok, "s": time.perf_counter() - t0, "brut": None if ok else txt[:120]}


async def _mesure(base, mode, documents=200, concurrence=8):
    import asyncio
    import aiohttp

    sem = asyncio.Semaphore(concurrence)
    timeout = aiohttp.ClientTimeout(total=900)

    async def garde(i, session):
        async with sem:
            return await _un(session, base, i, mode)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        t0 = time.perf_counter()
        res = await asyncio.gather(*[garde(i, session) for i in range(documents)])
        duree = time.perf_counter() - t0
    valides = sum(1 for r in res if r.get("ok"))
    erreurs = [r.get("err") for r in res if not r.get("ok") and r.get("err")]
    return {"mode": mode, "documents": documents, "valides": valides,
            "taux_validation": round(100 * valides / documents, 1),
            "secondes": round(duree, 1),
            "documents_par_heure": round(3600 * documents / duree, 1),
            "documents_valides_par_heure": round(3600 * valides / duree, 1),
            "concurrence": concurrence, "erreurs": erreurs[:3],
            "exemple_invalide": next((r.get("brut") for r in res if r.get("brut")), None)}


@app.function(image=IMAGE, gpu="H100", timeout=2400, memory=32768)
def campagne() -> dict:
    import asyncio
    import subprocess

    import aiohttp

    proc = subprocess.Popen(
        ["vllm", "serve", MOE, "--port", str(PORT), "--host", "0.0.0.0",
         "--tensor-parallel-size", "1", "--max-model-len", str(MAXLEN),
         "--gpu-memory-utilization", UTIL, "--disable-log-requests",
         "--enable-prefix-caching", "--served-model-name", "m"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{PORT}"
    t0 = time.time()
    try:
        while time.time() - t0 < 2100:
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
            return {"erreur": "serveur non prêt"}
        pret = time.time() - t0

        # quel mode de contrainte ce serveur accepte-t-il réellement ?
        choisi, essais = None, []
        for mode in ("response_format", "guided_json"):
            r = asyncio.run(_mesure(base, mode, documents=4, concurrence=1))
            essais.append({"mode": mode, "valides": r["valides"], "detail": r.get("exemple_invalide")})
            if r["valides"] > 0:
                choisi = mode
                break
        if choisi is None:
            return {"ready_seconds": round(pret, 1), "essais": essais,
                    "conclusion": "aucun mode de contrainte accepté"}
        mesure = asyncio.run(_mesure(base, choisi))
        return {"ready_seconds": round(pret, 1), "essais": essais, "mode_retenu": choisi,
                "mesure": mesure}
    finally:
        proc.kill()


@app.local_entrypoint()
def main():
    t0 = time.time()
    res = campagne.remote()
    with open(str(Path(__file__).with_name("extract.raw")), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("RESULT " + json.dumps(res, ensure_ascii=False))
    print(f"DUREE_TOTALE_S {time.time() - t0:.0f} | COUT_ESTIME_USD {(time.time() - t0)/3600*3.95:.2f}")
