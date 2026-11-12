#!/usr/bin/env python3
"""L'alternative qu'on n'avait pas mesurée : text-to-SQL de bout en bout.

Question : « et si on laissait simplement le modèle écrire le SQL ? » Réponse mesurée, sur les mêmes
questions que notre chaîne, avec le même modèle qui écrit (un modèle de calibre production, via API
pour la seule mesure).

Ce qu'on compte, et dans cet ordre d'importance :
  - nombre juste          : le SQL s'exécute et rend le bon chiffre
  - **nombre faux présenté sans doute** : chiffre différent de la référence, rendu sans réserve
  - erreur SQL            : la requête ne s'exécute pas (le cas le moins dangereux)
  - refus                 : le modèle refuse de répondre

    MUNITO_API_KEY=... python3 bench_sql_direct.py --model deepseek-ai/deepseek-v4-flash
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brain  # noqa: E402
from bench_traducteur import CAS  # noqa: E402

BASE = "https://api.munito.ai/inference/v1"
DB = Path(__file__).resolve().parent / "demo.sqlite"

SCHEMA = """
CREATE TABLE patients(id TEXT, annee_naissance INTEGER, sexe TEXT, service TEXT, date_inclusion TEXT);
CREATE TABLE sejours(id INTEGER, patient TEXT, entree TEXT, sortie TEXT, service TEXT, motif TEXT);
CREATE TABLE diagnostics(id INTEGER, patient TEXT, code_cim10 TEXT, libelle TEXT, date_diag TEXT,
                          valide_anapath INTEGER);
CREATE TABLE grossesses(id INTEGER, patient TEXT, date_fin TEXT);
CREATE TABLE gene_x(id INTEGER, patient TEXT, statut TEXT, date_prelevement TEXT);
"""


def reference(question: str) -> tuple[str, float] | None:
    """Vérité terrain : notre chaîne (règles + catalogue). Renvoie (nature, valeur).

    Attention au piège que j'ai posé moi-même : pour « âge moyen », la référence est la moyenne, pas
    l'effectif. Comparer les deux ferait passer une bonne réponse pour une faute.
    """
    r = brain.repondre(question, mode="regles")
    if r.get("statut") != "verifie" or not r.get("resultat"):
        return None
    res = r["resultat"]
    if "âge moyen" in question.lower() or "age moyen" in question.lower():
        return ("moyenne", float(res.get("age_moyen") or 0))
    return ("compte", float(res.get("n") or 0))


def demander_sql(model: str, question: str, key: str) -> str | None:
    prompt = (
        "Tu es un analyste de données. Écris UNE requête SQL SQLite qui répond à la question.\n"
        f"Schéma :\n{SCHEMA}\n"
        "Règles : cancer du sein confirmé = code_cim10 LIKE 'C50%' ET valide_anapath = 1. "
        "Âge = 2026 - annee_naissance. Grossesse antérieure = au moins une ligne dans grossesses. "
        "Gène X exprimé = statut = 'positif'. Si la question ne peut pas être répondue avec ce schéma, "
        "réponds exactement : IMPOSSIBLE.\n"
        "Réponds uniquement par le SQL, sans explication ni balise.\n"
        f"Question : {question}"
    )
    for essai, budget in enumerate((2500, 4000)):
        payload = {"model": model, "temperature": 0, "max_tokens": budget,
                   "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            raw = json.loads(urllib.request.urlopen(req, timeout=300).read().decode())
        except Exception:                                      # noqa: BLE001
            return None
        msg = raw["choices"][0]["message"]
        contenu = (msg.get("content") or "").strip()
        if not contenu:                                        # modèle de raisonnement : le texte est ailleurs
            contenu = (msg.get("reasoning_content") or "").strip()
        if contenu:
            # on garde le dernier bloc qui ressemble à du SQL
            import re as _re
            blocs = list(_re.finditer(r"(?is)\b(select|with)\b.*", contenu))
            return (blocs[-1].group(0) if blocs else contenu).strip()
    return None


def executer(sql: str) -> tuple[float | None, str | None]:
    sql = sql.replace("```sql", "").replace("```", "").strip()
    if not sql.upper().startswith(("SELECT", "WITH")):
        return None, "pas un SELECT"
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(sql).fetchall()
    except Exception as e:                                     # noqa: BLE001
        return None, type(e).__name__
    finally:
        con.close()
    if not rows:
        return None, "0 ligne"
    val = rows[0][0]
    return (float(val) if isinstance(val, (int, float)) else None), None


def run(model: str, key: str) -> dict:
    lignes = []
    for question, _, _ in CAS:
        ref = reference(question)
        sql = demander_sql(model, question, key)
        if sql is None:
            verdict, valeur, err = "erreur_appel", None, "pas de réponse"
        elif "IMPOSSIBLE" in sql.upper():
            verdict, valeur, err = ("refus_correct" if ref is None else "refus_a_tort"), None, None
        else:
            valeur, err = executer(sql)
            if err:
                verdict = "erreur_sql"
            elif ref is None:
                verdict = "repond_quand_il_ne_faut_pas"
            elif valeur is not None and abs(valeur - ref[1]) <= (0.2 if ref[0] == "moyenne" else 0.5):
                verdict = "juste"
            else:
                verdict = "faux_sans_doute"
        lignes.append({"question": question[:60], "reference": ref[1] if ref else None, "valeur": valeur,
                       "verdict": verdict, "erreur": err, "sql": (sql or "")[:200]})
    from collections import Counter
    compte = Counter(l["verdict"] for l in lignes)
    return {"model": model, "cas": len(CAS), "compte": dict(compte), "lignes": lignes}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="deepseek-ai/deepseek-v4-flash")
    a = ap.parse_args()
    key = os.environ.get("MUNITO_API_KEY", "")
    res = run(a.model, key)
    Path(__file__).with_name("bench_sql_direct.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{a.model} : {res['compte']}")
    for l in res["lignes"]:
        print(f"  {l['verdict']:26} ref={l['reference']} obtenu={l['valeur']} | {l['question'][:44]}")
