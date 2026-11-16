#!/usr/bin/env python3
"""Rejoue les 40 questions du carnet contre le noyau, et garde la trace.

Usage
-----
    python3 run40.py                     # http://localhost:8090, journal dans questions/
    python3 run40.py --base http://127.0.0.1:8099
    python3 run40.py --strict            # sortie 1 si un statut diffère du carnet

Ce que fait le script
---------------------
Il envoie les 40 questions de `questions/questions_medecins.json` à `/api/ask`, une par une,
en mode « regles » (aucun modèle, aucun GPU), et compare le statut obtenu au statut consigné
dans le carnet (`verifie`, `clarification`, `refus`, `conversation`).

Deux fichiers sont écrits dans `questions/` :
  - `run40-<horodatage>.log`  : lisible, une ligne par question + un résumé chiffré ;
  - `run40-<horodatage>.json` : le même contenu en machine (statuts, n, latences, message).

Le script ne « juge » pas la vérité clinique : il vérifie que la chaîne répond la même chose
qu'au tag de référence. Un chiffre qui change d'un run à l'autre est un défaut de reproductibilité,
et c'est exactement ce qu'un relecteur doit pouvoir constater sans nous croire.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
CARNET = HERE / "questions" / "questions_medecins.json"
SORTIE = HERE / "questions"


def attendre(base: str, limite_s: float = 60.0) -> None:
    """Attend que le noyau réponde, puis rend la main (ou lève une erreur lisible)."""
    debut = time.monotonic()
    dernier = ""
    while time.monotonic() - debut < limite_s:
        try:
            with urllib.request.urlopen(base + "/", timeout=3) as r:
                if r.status == 200:
                    return
                dernier = f"HTTP {r.status}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            dernier = str(e)
        time.sleep(1.0)
    raise SystemExit(f"noyau injoignable sur {base} après {limite_s:.0f} s ({dernier})")


def demander(base: str, question: str, role: str, timeout: float) -> dict:
    corps = json.dumps({"q": question, "role": role, "mode": "regles"}).encode()
    req = urllib.request.Request(base + "/api/ask", data=corps,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser(description="Rejoue les 40 questions du carnet.")
    ap.add_argument("--base", default="http://localhost:8090", help="URL du démonstrateur")
    ap.add_argument("--role", default="chercheur", help="rôle envoyé au noyau")
    ap.add_argument("--timeout", type=float, default=30.0, help="délai par question (s)")
    ap.add_argument("--strict", action="store_true",
                    help="sortie 1 si un statut ou un effectif diffère du carnet")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    carnet = json.loads(CARNET.read_text(encoding="utf-8"))
    if len(carnet) != 40:
        raise SystemExit(f"carnet inattendu : {len(carnet)} questions au lieu de 40")

    attendre(base)
    horodatage = time.strftime("%Y%m%d-%H%M%S")
    log_path = SORTIE / f"run40-{horodatage}.log"
    json_path = SORTIE / f"run40-{horodatage}.json"

    lignes: list[str] = []
    enregistrements: list[dict] = []
    ecarts: list[str] = []
    erreurs = 0

    debut = time.monotonic()
    for i, item in enumerate(carnet, start=1):
        question = item["q"]
        attendu = item.get("statut")
        t0 = time.monotonic()
        try:
            res = demander(base, question, args.role, args.timeout)
        except Exception as e:  # noqa: BLE001 — on veut la trace, pas un crash
            erreurs += 1
            res = {"statut": "ERREUR", "message": f"{type(e).__name__}: {e}"}
        ms = int((time.monotonic() - t0) * 1000)
        obtenu = res.get("statut")
        resultat = res.get("resultat")
        n = res.get("n")
        if n is None and isinstance(resultat, dict):
            n = resultat.get("n")
        marque = "ok" if obtenu == attendu else "ECART"
        if obtenu != attendu:
            ecarts.append(f"Q{i:02d} attendu={attendu} obtenu={obtenu}")
        if obtenu == "rejete":
            erreurs += 1
        lignes.append(
            f"Q{i:02d} [{marque}] {item.get('profil','?'):<22} {obtenu:<13} n={n if n is not None else '—':<5} {ms:>5} ms  {question[:70]}"
        )
        enregistrements.append({
            "n": i, "profil": item.get("profil"), "question": question,
            "attendu": attendu, "statut": obtenu, "effectif": n, "ms": ms,
            "message": res.get("message", ""), "mode": res.get("mode"),
            "ecart": obtenu != attendu,
        })

    duree = time.monotonic() - debut
    compte = Counter(e["statut"] for e in enregistrements)
    latences = sorted(e["ms"] for e in enregistrements)
    p50 = latences[len(latences) // 2]
    p95 = latences[min(len(latences) - 1, int(round(0.95 * (len(latences) - 1))))]

    resume = [
        "",
        "=" * 78,
        f"Résumé — {len(enregistrements)} questions en {duree:.1f} s contre {base}",
        "  " + " · ".join(f"{k}={v}" for k, v in sorted(compte.items())),
        f"  latence p50 {p50} ms · p95 {p95} ms · erreurs internes {erreurs}",
        f"  écarts avec le carnet : {len(ecarts)}" + (" — " + "; ".join(ecarts) if ecarts else " (aucun)"),
        "=" * 78,
    ]

    entete = [
        f"# Carnet de 40 questions — exécution du {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"# base={base} role={args.role} mode=regles",
        "# statut attendu = celui consigné dans questions/questions_medecins.json (tag note-2026-11-12 + couche conversationnelle)",
        "",
    ]
    contenu = "\n".join(entete + lignes + resume) + "\n"
    log_path.write_text(contenu, encoding="utf-8")
    json_path.write_text(json.dumps({
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base": base, "role": args.role, "mode": "regles",
        "duree_s": round(duree, 1), "compte": dict(compte),
        "p50_ms": p50, "p95_ms": p95, "erreurs": erreurs, "ecarts": ecarts,
        "questions": enregistrements,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(contenu)
    print(f"log  : {log_path.relative_to(HERE)}")
    print(f"json : {json_path.relative_to(HERE)}")

    if args.strict and (ecarts or erreurs):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
