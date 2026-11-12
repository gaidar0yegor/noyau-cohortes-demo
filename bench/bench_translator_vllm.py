#!/usr/bin/env python3
"""Qwen3-4B comme traducteur, sur 40 questions — le seul point resté non mesuré.

Le modèle tourne sur une H100 distante (vLLM, décodage contraint par schéma). La base, le validateur et la
comparaison restent **locaux** : aucune donnée ne quitte la machine, et c'est la même chaîne de validation
que dans le démonstrateur.

    python3 bench_translator_vllm.py --url http://127.0.0.1:8000 --model qwen3-4b
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import brain  # noqa: E402

BANK = json.loads((HERE / "questions_medecins.json").read_text(encoding="utf-8"))

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["count", "mean_age", "autre"]},
        "filters": {"type": "array", "items": {"type": "string"}},
        "age": {"type": ["array", "null"], "items": {"type": "integer"},
                "minItems": 2, "maxItems": 2},
        "service": {"type": ["string", "null"], "enum": ["A", "B", "C", "D", None]},
        "non_couverts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent", "filters", "age", "service", "non_couverts"],
    "additionalProperties": False,
}


def plan_du_modele(base: str, modele: str, question: str) -> dict:
    concepts = ", ".join(brain.SEMANTIQUE)
    prompt = (
        f"Concepts autorisés : {concepts}.\n"
        "Transforme la question en objet JSON : intent (count|mean_age|autre), filters (liste de concepts "
        "autorisés, préfixe NOT si négation), age ([min,max] ou null), service (A|B|C|D ou null), "
        "non_couverts (termes métier de la question qu'aucun concept ne couvre).\n"
        f"Question : {question}"
    )
    corps = {"model": modele, "temperature": 0, "max_tokens": 400,
             "messages": [{"role": "user", "content": prompt}],
             "response_format": {"type": "json_schema",
                                 "json_schema": {"name": "plan", "schema": SCHEMA}}}
    req = urllib.request.Request(f"{base}/v1/chat/completions", data=json.dumps(corps).encode(),
                                 headers={"Content-Type": "application/json"})
    raw = json.loads(urllib.request.urlopen(req, timeout=180).read().decode())
    texte = raw["choices"][0]["message"]["content"]
    return json.loads(texte)


def normaliser(p: dict) -> dict:
    age = p.get("age")
    if isinstance(age, list) and len(age) == 2 and all(isinstance(a, int) for a in age) and age[0] < age[1]:
        age = [age[0], age[1]]
    else:
        age = None
    plan = {
        "intent": p.get("intent") if p.get("intent") in ("count", "mean_age") else None,
        "filters": [f for f in (p.get("filters") or []) if isinstance(f, str)],
        "age": age,
        "age_ref": None,
        "service": p.get("service") if p.get("service") in ("A", "B", "C", "D") else None,
        "inconnus": [],
        "source_plan": "modele:vllm",
    }
    plan["inconnus"] = [f.replace("NOT ", "") for f in plan["filters"]
                        if f.replace("NOT ", "") not in brain.SEMANTIQUE]
    decl = p.get("non_couverts") or []
    if isinstance(decl, list):
        plan["inconnus"] += brain._termes_reellement_inconnus(decl, plan["filters"])
    return plan


def executer(plan: dict) -> tuple[int | None, str | None]:
    """Même ordre de décision que repondre() : valider, exiger un critère, justifier, compiler, compter."""
    erreurs = brain.valider(plan)
    if erreurs:
        return None, "refus_plan"
    if plan["intent"] in ("count", "mean_age") and not plan["filters"] and not plan["age"] and not plan["service"]:
        return None, "refus_sans_critere"
    if brain.justifier(plan, plan.get("_question", "")):
        return None, "refus_justification"
    sql, params = brain.compiler(plan)
    con = brain.connect()
    n = dict(con.execute(sql, params).fetchone()).get("n") or 0
    con.close()
    if n < brain.SEUIL_N:
        return None, "refus_effectif"
    return int(n), None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--model", default="qwen3-4b")
    ap.add_argument("--out", default="translator_4b.json")
    a = ap.parse_args()

    lignes, compte = [], {}
    for cas in BANK:
        q = cas["q"]
        try:
            plan = normaliser(plan_du_modele(a.url, a.model, q))
            plan["_question"] = q
            n, refus = executer(plan)
            statut = "verifie" if n is not None else refus
        except Exception as e:                                # noqa: BLE001
            plan, n, statut = {}, None, "erreur:" + type(e).__name__
        # comparaison à la chaîne de règles, qui fait référence
        ref = cas["statut"]
        verdict = "ok" if statut == ref else ("faux" if statut == "verifie" and ref != "verifie" else "different")
        if statut == "verifie" and ref == "verifie" and n != cas.get("n"):
            verdict = "faux"
        compte[statut] = compte.get(statut, 0) + 1
        lignes.append({"q": q, "attendu": ref, "obtenu": statut, "n": n, "n_ref": cas.get("n"),
                       "verdict": verdict, "plan": {k: v for k, v in plan.items() if k != "_question"}})

    faux = [l for l in lignes if l["verdict"] == "faux"]
    res = {"modele": a.model, "statuts": compte, "faux": len(faux),
           "meme_verdict": sum(1 for l in lignes if l["verdict"] == "ok"), "lignes": lignes}
    pathlib.Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("modele", "statuts", "faux", "meme_verdict")},
                     ensure_ascii=False))
    for l in faux[:8]:
        print("  FAUX:", l["q"][:60], "| получено", l["n"], "| эталон", l["n_ref"], "|", l["obtenu"])


if __name__ == "__main__":
    main()
