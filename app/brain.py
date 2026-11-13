#!/usr/bin/env python3
"""Démonstrateur « cerveau IA » sur données synthétiques — aucune dépendance externe.

Ce que ça montre, et rien d'autre :
  1. une question en français ne devient pas une réponse : elle devient un PLAN DE REQUÊTE ;
  2. le plan est validé contre une couche sémantique versionnée (champ inconnu = refus, pas invention) ;
  3. le SQL est compilé et journalisé avec la réponse ;
  4. tout nombre du texte final est vérifié contre les champs du résultat (sinon la réponse est rejetée) ;
  5. un effectif trop petit (n < 10) est un refus explicite, pas un arrondi.

Lancement :  python3 demo_brain.py            → http://127.0.0.1:8099
             python3 demo_brain.py --ask "combien de patientes ont un cancer du sein confirmé ?"
"""
from __future__ import annotations

import json
import math
import random
import re
import sqlite3
import sys
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
DB = HERE / "demo.sqlite"
JOURNAL = HERE / "journal.jsonl"
TODAY = date(2026, 11, 9)
SEUIL_N = 10          # seuil de restitution : en dessous, on refuse
SEUIL_CELLULE = 5     # aucune cellule < 5 dans un croisement
REF_FREQ = 0.35       # fréquence de référence du gène X dans la cohorte

# ----------------------------------------------------------------------------------------------
# 1. Données synthétiques — déterministes, donc rejouables
# ----------------------------------------------------------------------------------------------

def build_db() -> None:
    if DB.exists():
        DB.unlink()
    rnd = random.Random(42)
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE patients(id TEXT PRIMARY KEY, annee_naissance INTEGER, sexe TEXT,
                          service TEXT, date_inclusion TEXT);
    CREATE TABLE sejours(id INTEGER PRIMARY KEY, patient TEXT, entree TEXT, sortie TEXT,
                         service TEXT, motif TEXT);
    CREATE TABLE diagnostics(id INTEGER PRIMARY KEY, patient TEXT, code_cim10 TEXT, libelle TEXT,
                             date_diag TEXT, valide_anapath INTEGER);
    CREATE TABLE grossesses(id INTEGER PRIMARY KEY, patient TEXT, date_fin TEXT);
    CREATE TABLE gene_x(id INTEGER PRIMARY KEY, patient TEXT, statut TEXT, date_prelevement TEXT);
    """)
    pats, sej, diag, gross, gene = [], [], [], [], []
    for i in range(3000):
        pid = f"PT-{i:05d}"
        # 3 % sans année de naissance connue : c'est le piège « manquant ≠ zéro »
        annee = None if rnd.random() < 0.03 else rnd.randint(1936, 2005)
        service = rnd.choice(["A", "B", "C", "D"])
        pats.append((pid, annee, "F" if rnd.random() < 0.62 else "M", service,
                     (date(2019, 1, 1) + timedelta(days=rnd.randint(0, 2500))).isoformat()))
        for k in range(rnd.randint(0, 4)):
            entree = date(2020, 1, 1) + timedelta(days=rnd.randint(0, 2300))
            sortie = entree + timedelta(days=rnd.randint(1, 20))
            sej.append((len(sej) + 1, pid, entree.isoformat(),
                        None if rnd.random() < 0.08 else sortie.isoformat(),
                        service, "hospitalisation"))
        if rnd.random() < 0.35:
            d = date(2020, 1, 1) + timedelta(days=rnd.randint(0, 2300))
            sev = rnd.choice(["C50.1", "C50.3", "C50.9", "C34.1", "C18.2", "I10"])
            diag.append((len(diag) + 1, pid, sev,
                         "Tumeur maligne du sein" if sev.startswith("C50") else "autre",
                         d.isoformat(), 1 if rnd.random() < 0.8 else 0))
        if rnd.random() < 0.40:
            gross.append((len(gross) + 1, pid,
                          (date(2015, 1, 1) + timedelta(days=rnd.randint(0, 3000))).isoformat()))
        if rnd.random() < 0.45:
            gene.append((len(gene) + 1, pid,
                         "positif" if rnd.random() < REF_FREQ else
                         ("non_fait" if rnd.random() < 0.15 else "negatif"),
                         (date(2021, 1, 1) + timedelta(days=rnd.randint(0, 1600))).isoformat()))
    con.executemany("INSERT INTO patients VALUES (?,?,?,?,?)", pats)
    con.executemany("INSERT INTO sejours VALUES (?,?,?,?,?,?)", sej)
    con.executemany("INSERT INTO diagnostics VALUES (?,?,?,?,?,?)", diag)
    con.executemany("INSERT INTO grossesses VALUES (?,?,?)", gross)
    con.executemany("INSERT INTO gene_x VALUES (?,?,?,?)", gene)

    # Le cas de David : la tranche 25–36 sans grossesse antérieure doit contenir exactement
    # 16 patientes (dont 14 positives au gène X) — sinon l'exemple ne se rejoue pas.
    tranche = [r[0] for r in con.execute("""
        SELECT DISTINCT p.id FROM patients p
        JOIN diagnostics d ON d.patient = p.id
        WHERE d.code_cim10 LIKE 'C50%' AND d.valide_anapath = 1
          AND (2026 - p.annee_naissance) BETWEEN 25 AND 36
          AND p.id NOT IN (SELECT patient FROM grossesses)
        ORDER BY p.id""").fetchall()]
    en_trop = tranche[16:]
    con.executemany("UPDATE diagnostics SET valide_anapath = 0 WHERE patient = ?",
                    [(p,) for p in en_trop])
    cible = tranche[:16]
    con.executemany("DELETE FROM gene_x WHERE patient = ?", [(p,) for p in cible])
    for j, p in enumerate(cible):
        con.execute("INSERT INTO gene_x VALUES (?,?,?,?)",
                    (100000 + j, p, "positif" if j < 14 else "negatif", "2026-02-01"))
    con.commit()
    con.close()


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


# ----------------------------------------------------------------------------------------------
# 2. Couche sémantique — versionnée, et c'est elle qui décide de ce qui est calculable
# ----------------------------------------------------------------------------------------------

MOTS_VIDES = {
    "combien", "quel", "quelle", "quels", "quelles", "est", "sont", "l", "la", "le", "les", "des",
    "de", "du", "un", "une", "dans", "avec", "sans", "sur", "pour", "et", "ou", "que", "qui", "au",
    "aux", "en", "par", "nombre", "effectif", "moyen", "moyenne", "âge", "age", "ans", "an", "ont",
    "toutes", "tous", "elles", "ils", "patiente", "patientes", "patient", "patients", "a", "y",
    "d", "s", "c", "j", "leurs", "leur", "ce", "cet", "cette", "ces",
}


# Vocabulaire structurel : les mots qui organisent la question sans porter de concept métier.
MOTS_STRUCTURELS = {
    "prise", "prises", "charge", "concerne", "concernent", "concernees", "recu", "recus", "recue", "recues",
    "eu", "fait", "faite", "faites", "realise", "realisee", "realisees", "depuis", "dernier", "derniere",
    "derniers", "dernieres", "mois", "annee", "annees", "jour", "jours", "nouveau", "nouveaux", "nouvelle",
    "cas", "repartition", "distribution", "duree", "sejour", "sejours", "motif", "motifs", "manquant",
    "manquante", "manquants", "renseigne", "renseignee", "renseignes", "doublon", "doublons", "eligible",
    "eligibles", "essai", "critere", "criteres", "inclusion", "valide", "validee", "valides",
    "frequence", "proportion",
    "pourcentage", "taux", "total", "ensemble", "ici", "chez", "moins", "plus", "entre", "selon", "par",
    "chaque", "toutes", "tous", "aucune", "aucun", "aucunes", "plusieurs", "meme", "memes", "aussi",
    "service", "services", "dossier", "dossiers", "medecin", "medecins", "clinique", "cliniques",
    "cohorte", "cohortes", "base", "bases", "donnee", "donnees", "fichier", "fichiers", "table",
    "ete", "expriment", "parmi", "celles", "ceux", "apres", "avant", "deux", "trois", "quatre", "a",
    "anterieure", "anterieur", "confirme", "confirmee", "confirmees", "documente", "documentee", "enregistrement", "enregistrements",
    "meme", "memes", "cancer", "cancers", "sein", "seins", "confondus", "confondues", "trimestre",
    "semestre", "mois", "an", "annee", "annees", "premier", "premiere", "second", "deuxieme", "diagnostic", "diagnostics", "diag", "date", "dates", "aujourd", "hui", "aujourdhui", "actuel", "actuelle",
    "date", "moment",
}


def _sans_accent(w: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", w) if unicodedata.category(c) != "Mn")


def couverture(q: str, plan: dict) -> list[str]:
    """La direction manquante de la garde : chaque mot porteur doit être justifié par le plan.

    Sans elle, « combien de patientes dans l'hôpital voisin ? » renvoie l'effectif total : un chiffre
    faux, présenté sans réserve. C'est le seul mode de défaillance qu'on ne peut pas laisser passer.
    """
    t = q.lower()
    couverts: set[str] = set()
    for c in plan["filters"]:
        base = c.replace("NOT ", "")
        for d in SEMANTIQUE.get(base, {}).get("declencheurs", []):
            couverts.update(re.findall(r"[a-zà-ÿ]+", d.lower()))
    if plan.get("age"):
        couverts.update({"moins", "plus", "entre", "et", "ans", "age"})
    if plan.get("service"):
        couverts.update({"service", plan["service"].lower()})
    if plan.get("intent") == "mean_age":
        couverts.update({"age", "moyen", "moyenne"})
    MOTS_VIDES_N = {_sans_accent(w) for w in MOTS_VIDES}
    MOTS_STRUCT_N = {_sans_accent(w) for w in MOTS_STRUCTURELS}
    couverts = {_sans_accent(w) for w in couverts}
    libres = []
    for w in re.findall(r"[a-zà-ÿ]+", t):
        w = _sans_accent(w)
        if w in MOTS_VIDES_N or w in MOTS_STRUCT_N or w in couverts or w.isdigit():
            continue
        # morphologie : « hospitalisées » couvre « hospitalis », « confirmées » couvre « confirmé »
        if any(len(w) >= 5 and (w.startswith(c) or c.startswith(w)) for c in couverts if len(c) >= 4):
            continue
        libres.append(w)
    return libres


SEMANTIQUE = {
    "patiente_active": {
        "version": "v3",
        "declencheurs": ["active", "actives", "suivie", "suivies", "en cours"],
        "libelle": "Patiente active : séjour ouvert ou dernière sortie < 24 mois",
        "source": "S1 · sejours",
        "sql": """EXISTS (SELECT 1 FROM sejours s WHERE s.patient = p.id
                      AND COALESCE(s.sortie, '9999-12-31') >= date('{today}', '-24 months'))""",
        "valide_par": "Dr. — (à valider)",
    },
    "cancer_du_sein_confirme": {
        "version": "v2",
        "declencheurs": ["cancer du sein", "c50", "sein"],
        "libelle": "Cancer du sein confirmé : code CIM-10 C50.* ET validation anatomopathologique",
        "source": "S1 · diagnostics",
        "sql": """EXISTS (SELECT 1 FROM diagnostics d WHERE d.patient = p.id
                      AND d.code_cim10 LIKE 'C50%' AND d.valide_anapath = 1)""",
        "valide_par": "Dr. — (à valider)",
    },
    "grossesse_anterieure": {
        "version": "v1",
        "declencheurs": ["grossesse", "enfant", "enfants"],
        "libelle": "Grossesse antérieure : au moins une grossesse enregistrée avant le diagnostic",
        "source": "S1 · grossesses",
        "sql": """EXISTS (SELECT 1 FROM grossesses g WHERE g.patient = p.id)""",
        "valide_par": "Dr. — (à valider)",
    },
    "cancer_du_sein_toute_mention": {
        "version": "v1",
        "declencheurs": ["toute mention", "c50"],
        "libelle": "Toute mention de C50, sans exigence de confirmation anatomopathologique",
        "source": "S1 · diagnostics",
        "sql": """EXISTS (SELECT 1 FROM diagnostics d WHERE d.patient = p.id
                      AND d.code_cim10 LIKE 'C50%')""",
        "valide_par": "Dr. — (à valider)",
    },
    "expression_gene_x": {
        "version": "v1",
        "declencheurs": ["gène x", "gene x", "expression"],
        "libelle": "Expression du gène X : prélèvement positif ('non_fait' n'est pas 'négatif')",
        "source": "S1 · gene_x",
        "sql": """EXISTS (SELECT 1 FROM gene_x gx WHERE gx.patient = p.id AND gx.statut = 'positif')""",
        "valide_par": "Dr. — (à valider)",
    },
}


# ----------------------------------------------------------------------------------------------
# La couche sémantique est un FICHIER, pas du code : un clinicien peut la lire, git peut la versionner.
# ----------------------------------------------------------------------------------------------

def charger_catalogue(chemin: Path | None = None) -> dict:
    """Charge semantique.yml s'il existe ; sinon on garde le dictionnaire ci-dessus (repli)."""
    fichier = chemin or (HERE / "semantique.yml")
    if not fichier.exists():
        return SEMANTIQUE
    import yaml
    data = yaml.safe_load(fichier.read_text(encoding="utf-8")) or {}
    concepts = {}
    for nom, c in (data.get("concepts") or {}).items():
        concepts[nom] = {
            "version": c.get("version", "v1"),
            "libelle": c.get("libelle", nom),
            "source": c.get("source", ""),
            "sql": " ".join((c.get("sql") or "").split()),
            "valide_par": c.get("valide_par", "—"),
            "declencheurs": c.get("declencheurs") or [],
        }
    return concepts or SEMANTIQUE


SEMANTIQUE = charger_catalogue()

# ----------------------------------------------------------------------------------------------
# 3. Traducteur : question → plan. Règles explicites, pas de modèle ici (c'est le principe)
# ----------------------------------------------------------------------------------------------

def traduire(q: str) -> dict:
    t = q.lower()
    plan: dict = {"intent": None, "filters": [], "aggregate": None, "service": None,
                  "age": None, "age_ref": None, "inconnus": []}

    if re.search(r"\bcombien\b|\bnombre\b|\beffectif\b|\bfr[ée]quence\b", t):
        plan["intent"] = "count"
    elif re.search(r"âge moyen|age moyen|moyenne d'âge|moyenne d.age", t):
        plan["intent"] = "mean_age"
    else:
        plan["inconnus"].append("intention (compter ? moyenner ? croiser ?)")

    if re.search(r"toute mention", t):
        plan["filters"].append("cancer_du_sein_toute_mention")
    elif re.search(r"cancer du sein|c50", t):
        plan["filters"].append("cancer_du_sein_confirme")
    if re.search(r"gène x|gene x|expression", t):
        plan["filters"].append("expression_gene_x")
    if re.search(r"grossesse", t) and re.search(r"(après|apres|avant) \d{1,3} ans", t):
        # « grossesse après 35 ans » parle de l'âge au moment de la grossesse, que la base ne porte pas
        plan["inconnus"].append("âge au moment de la grossesse")
    if re.search(r"sans grossesse|jamais eu d.enfant|sans enfant", t):
        plan["filters"].append("NOT grossesse_anterieure")
    elif re.search(r"grossesse", t):
        plan["filters"].append("grossesse_anterieure")
    if re.search(r"active|suivie|en cours de suivi", t):
        plan["filters"].append("patiente_active")
    if re.search(r"hospitalis", t):
        plan["filters"].append("hospitalisation_recente")
    if re.search(r"encore hospitalisée|en cours d'hospitalisation", t):
        plan["filters"].append("sejour_ouvert")
    if re.search(r"cancer du poumon|cancer du colon|cancer du côlon|cancer de la prostate", t):
        # on sait qu'il existe d'autres tumeurs, mais pas *laquelle* sans le référentiel complet :
        # répondre le nombre d'« autres tumeurs » à une question sur le poumon serait un chiffre faux
        plan["inconnus"].append("localisation tumorale précise")
    elif re.search(r"autre tumeur", t):
        plan["filters"].append("autre_tumeur_maligne")
    if re.search(r"non fait|non réalisé|non realise|pas prélevé", t):
        plan["filters"].append("gene_x_non_fait")

    m = re.search(r"entre (\d{1,3}) et (\d{1,3}) ans", t) or \
        re.search(r"(\d{1,3})\s*[-–]\s*(\d{1,3}) ans", t)
    if m:
        plan["age"] = [int(m.group(1)), int(m.group(2))]
    else:
        m = re.search(r"moins de (\d{1,3}) ans", t)
        if m:
            plan["age"] = [1, int(m.group(1))]
        else:
            m = re.search(r"plus de (\d{1,3}) ans", t)
            if m:
                plan["age"] = [int(m.group(1)), 119]
    if re.search(r"au diagnostic|à la date du diagnostic|a la date du diagnostic|lors du diagnostic", t):
        plan["age_ref"] = "diag"
    elif re.search(r"aujourd|actuel", t):
        plan["age_ref"] = "now"
    m2 = re.search(r"service ([a-d])\b", t)
    if m2:
        plan["service"] = m2.group(1).upper()

    # concepts évoqués mais absents de la couche sémantique → on refuse plutôt que d'inventer
    for mot, concept in [("allergie", "allergie"), ("poids", "poids"), ("tabac", "tabagisme"),
                         ("traitement", "traitement"), ("radio", "imagerie"),
                         ("âge moyen", "age_moyen")]:
        if mot in t and concept == "age_moyen" and plan["intent"] == "mean_age":
            continue
        if mot in t:
            plan["inconnus"].append(concept)
    # garde bidirectionnelle : les mots que le plan ne justifie pas deviennent un refus explicite
    for mot in couverture(q, plan):
        if mot not in plan["inconnus"]:
            plan["inconnus"].append(mot)
    return plan


def traduire_llm(q: str, model: str = "qwen3:1.7b", timeout: int = 60) -> dict:
    """Même contrat, mais le plan est proposé par un modèle local — et il se trompe parfois.

    C'est le point du démonstrateur : le modèle ne répond pas, il propose un plan ; le validateur
    et le journal décident ensuite. Un modèle qui invente un concept produit un refus, pas un chiffre.
    """
    import urllib.request

    concepts = ", ".join(SEMANTIQUE)
    prompt = (
        f"Concepts autorisés : {concepts}.\n"
        "Transforme la question en objet JSON avec les clés : intent (count|mean_age), "
        "filters (liste de concepts autorisés, préfixe NOT si négation), age ([min,max] ou null), "
        "service (A|B|C|D ou null), non_couverts (liste des mots de la question sans concept).\n"
        f"Question : {q}"
    )
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json",
                       "options": {"temperature": 0}}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode()
        text = json.loads(raw).get("response", "")
    except Exception as e:                                   # noqa: BLE001
        return {"intent": None, "filters": [], "age": None, "service": None,
                "inconnus": [], "erreur_modele": type(e).__name__}

    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"intent": None, "filters": [], "age": None, "service": None,
                "inconnus": [], "erreur_modele": "JSON absent"}
    try:
        p = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"intent": None, "filters": [], "age": None, "service": None,
                "inconnus": [], "erreur_modele": "JSON invalide"}

    age = p.get("age")
    if isinstance(age, list) and len(age) == 2 and all(isinstance(a, int) for a in age):
        age = [age[0], age[1]] if age[0] < age[1] else None
    else:
        age = None

    plan = {"intent": p.get("intent") if p.get("intent") in ("count", "mean_age") else None,
            "filters": [f for f in (p.get("filters") or []) if isinstance(f, str)],
            "age": age,
            "service": p.get("service") if p.get("service") in ("A", "B", "C", "D") else None,
            "inconnus": [], "source_plan": "modele:" + model}
    # un concept inventé, ou un mot non couvert que le modèle ose déclarer, est refusé par valider()
    plan["inconnus"] = [f.replace("NOT ", "") for f in plan["filters"]
                        if f.replace("NOT ", "") not in SEMANTIQUE]
    # un petit modèle met volontiers des mots ordinaires dans non_couverts : on les écarte,
    # sinon tout devient un refus. Ce qui reste, ce sont les termes métier non couverts.
    decl = p.get("non_couverts") or []
    if isinstance(decl, list):
        plan["inconnus"] += _termes_reellement_inconnus(decl, plan["filters"])
    else:
        plan["inconnus"].append("champ non_couverts mal formé")
    return plan


def _plan_schema() -> dict:
    """Schéma passé au décodeur : le modèle ne peut pas produire autre chose."""
    concepts = list(SEMANTIQUE) + ["NOT " + c for c in SEMANTIQUE]
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ["count", "mean_age"]},
            "filters": {"type": "array", "items": {"type": "string", "enum": concepts}},
            "age": {"type": ["array", "null"], "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            "service": {"type": ["string", "null"], "enum": ["A", "B", "C", "D", None]},
            "non_couverts": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["intent", "filters", "non_couverts"],
    }


def traduire_llm_schema(question: str, model: str = "qwen3:1.7b", timeout: int = 60) -> dict:
    """Même tâche, mais le décodeur est contraint par le schéma (guided decoding).

    Ce n'est plus « demande gentiment du JSON » : le modèle ne PEUT pas produire un plan hors
    schéma. La classe d'erreur « JSON invalide / concept inventé » disparaît par construction ;
    restent les erreurs de sens — et c'est là que la double lecture sert encore.
    """
    import urllib.request

    prompt = ("Transforme la question en plan de requête. Utilise uniquement les concepts du schéma. "
              "Mets dans non_couverts les termes métier qu'aucun concept ne couvre. "
              f"Question : {question}")
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "format": _plan_schema(), "options": {"temperature": 0}}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        raw = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
        p = json.loads(raw.get("response", "{}"))
    except Exception as e:                                   # noqa: BLE001
        return {"intent": None, "filters": [], "age": None, "service": None,
                "inconnus": [], "erreur_modele": type(e).__name__}

    plan = {"intent": p.get("intent"), "filters": p.get("filters") or [],
            "age": p.get("age"), "service": p.get("service"),
            "inconnus": [], "source_plan": "schema:" + model,
            "erreur_modele": None}
    plan["inconnus"] = [f.replace("NOT ", "") for f in plan["filters"] if f.replace("NOT ", "") not in SEMANTIQUE]
    decl = p.get("non_couverts") or []
    plan["inconnus"] += _termes_reellement_inconnus(decl, plan["filters"])
    return plan


def double_lecture(question: str, model: str = "qwen3:1.7b") -> tuple[dict, list[str]]:
    """Deux lectures indépendantes : les règles et le modèle local.

    Si elles ne trouvent pas le même ensemble de concepts, on ne répond pas. C'est la seule
    protection trouvée contre l'erreur la plus dangereuse : un plan valide qui a silencieusement
    oublié un filtre (donc un chiffre faux, présenté comme vérifié).
    """
    regles = traduire(question)
    modele = traduire_llm(question, model)
    a = {f.replace("NOT ", "") for f in regles["filters"]}
    b = {f.replace("NOT ", "") for f in modele["filters"] if f.replace("NOT ", "") in SEMANTIQUE}
    ecart = sorted(a.symmetric_difference(b)) + sorted({x for x in modele["inconnus"] if x not in a})
    problemes = []
    if ecart:
        problemes.append("les deux lectures divergent sur : " + ", ".join(ecart))
    if modele.get("erreur_modele"):
        problemes.append("le modèle n'a pas produit de plan exploitable (" + modele["erreur_modele"] + ")")
    if regles["age"] != modele["age"]:
        problemes.append("désaccord sur la tranche d'âge")
    if (regles["service"] or None) != (modele["service"] or None):
        problemes.append("désaccord sur le service")
    fusion = dict(modele)
    fusion["source_plan"] = "double_lecture"
    return fusion, problemes


def ambiguites(q: str) -> list[dict]:
    """Ce que le système ne peut pas décider seul : il pose la question au lieu de choisir.

    Le cas réel : « cancer du sein » ne dit pas si l'on exige la validation anatomopathologique.
    Deux définitions existent, versionnées séparément ; choisir à la place de l'utilisateur serait
    exactement le genre de décision silencieuse qu'on reproche aux outils « IA ».
    """
    t = q.lower()
    out = []
    if re.search(r"cancer du sein|c50", t) and not re.search(r"confirm|anapath|toute mention", t):
        # On exécute les deux définitions au lieu d'en décrire une : c'est le désaccord *chiffré*
        # qui rend la question utile. Si les deux donnent le même effectif, on ne demande rien.
        variantes = [
            {"label": "Cancer confirmé (CIM-10 + anapath)", "suffixe": "confirmé", "n": _compter(q + " confirmé")},
            {"label": "Toute mention de C50", "suffixe": "toute mention", "n": _compter(q + " toute mention")},
        ]
        # pour une moyenne, comparer des effectifs n'a pas de sens : on demande sans chiffres
        moyenne = bool(re.search(r"âge moyen|age moyen|moyenne d.âge", t))
        ens = [_ensemble(q + " " + v["suffixe"]) for v in variantes]
        identiques = all(e is not None for e in ens) and ens[0] == ens[1]
        ecart = jaccard(ens[0], ens[1]) if all(e is not None for e in ens) else None
        chiffrees = all(v["n"] is not None for v in variantes) and not moyenne
        question = None
        if identiques:
            # les deux définitions désignent exactement les mêmes patientes : on répond, on ne demande pas
            out.append({"champ": "__equivalent__", "question":
                        "Les deux définitions désignent exactement les mêmes patientes : je réponds sans vous "
                        "demander de choisir.", "options": [], "equivalent": True})
        elif chiffrees and len({v["n"] for v in variantes}) > 1:
            a, b = variantes
            question = (f"Avec la validation anatomopathologique : {a['n']} patientes. "
                        f"Sans elle : {b['n']}. Laquelle des deux définitions ?")
        else:
            # une des deux variantes n'est pas calculable telle quelle : on demande quand même,
            # sans inventer de chiffre pour celle qu'on ne sait pas exécuter
            question = ("Faut-il exiger la validation anatomopathologique ? Les deux définitions existent "
                        "dans le catalogue, et le chiffre change.")
        if question is not None:
            out.append({"champ": "définition du cancer", "question": question, "options": variantes})
    if plan_age_ambigu(q):
        n_diag = _compter(q + " au diagnostic")
        n_now = _compter(q + " aujourd'hui")
        e_diag, e_now = _ensemble(q + " au diagnostic"), _ensemble(q + " aujourd'hui")
        if e_diag is not None and e_now is not None and e_diag == e_now:
            out.append({"champ": "__equivalent__", "question":
                        "Les deux références d'âge désignent les mêmes patientes : je réponds sans demander.",
                        "options": [], "equivalent": True})
        elif n_diag is not None and n_now is not None and n_diag != n_now:
            out.append({
                "champ": "référence de l'âge",
                "question": (f"À la date du diagnostic : {n_diag} patientes. Âge aujourd'hui : {n_now}. "
                             f"Laquelle des deux références ?"),
                "options": [
                    {"label": "Âge au moment du diagnostic", "suffixe": "au diagnostic", "n": n_diag},
                    {"label": "Âge aujourd'hui", "suffixe": "aujourd'hui", "n": n_now},
                ],
            })
    return out


def plan_age_ambigu(q: str) -> bool:
    """Une tranche d'âge sans référence explicite : deux lectures possibles, deux chiffres."""
    t = q.lower()
    if re.search(r"au diagnostic|aujourd|actuel", t):
        return False
    return bool(re.search(r"entre \d{1,3} et \d{1,3} ans|moins de \d{1,3} ans|plus de \d{1,3} ans", t))



def _ensemble(question: str) -> set[str] | None:
    """Identifiants de patientes d'une variante — pour comparer des ensembles, pas des effectifs."""
    try:
        plan = traduire(question)
        if valider(plan):
            return None
        # pour une moyenne, compiler() rend AVG(...) et non des identifiants : on force un plan de comptage,
        # sinon on comparerait des ensembles faux et on croirait les définitions équivalentes
        plan["intent"] = "count"
        sql, params = compiler(plan)
        con = connect()
        ids = {r[0] for r in con.execute(sql.replace("COUNT(*) AS n", "p.id AS n"), params).fetchall()}
        con.close()
        return ids
    except Exception:                                       # noqa: BLE001
        return None


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _compter(question: str) -> int | None:
    """Effectif d'une variante de définition. Sert uniquement à chiffrer le désaccord."""
    try:
        plan = traduire(question)
        if valider(plan):
            return None
        sql, params = compiler(plan)
        con = connect()
        n = dict(con.execute(sql, params).fetchone()).get("n") or 0
        con.close()
        return int(n)
    except Exception:                                       # noqa: BLE001
        return None


def justifier(plan: dict, question: str) -> list[str]:
    """Contrôle de justification BIDIRECTIONNEL — la garde qui manque à tous les « text-to-SQL ».

    Sens 1 (couverture) : chaque terme métier de la question est couvert ou déclaré inconnu.
    Sens 2 (justification) : chaque filtre du plan est provoqué par un mot de la question.
    Sans le sens 2, un modèle peut ajouter un filtre que personne n'a demandé (« patiente active »)
    et rendre un chiffre faux qui a l'air parfaitement calculé. C'est exactement ce que fait un 12 B
    dans mon banc : plan valide, concept réel, réponse fausse.

    Déterministe, pas de modèle : des déclencheurs par concept, versionnés avec lui.
    """
    t = " " + re.sub(r"\s+", " ", question.lower()) + " "
    injustifies = []
    for f in plan.get("filters", []):
        nom = f.replace("NOT ", "")
        decl = SEMANTIQUE.get(nom, {}).get("declencheurs")
        if not decl:                     # concept sans déclencheurs : on ne juge pas
            continue
        if not any(d in t for d in decl):
            injustifies.append(nom)
    return injustifies


def _mots(texte: str) -> list[str]:
    import unicodedata
    t = unicodedata.normalize("NFKD", texte.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return [w for w in re.split(r"[^a-z0-9]+", t) if w]


def _vocabulaire(filters: list[str]) -> set[str]:
    """Mots déjà couverts par les concepts retenus (leur libellé), plus les mots de service."""
    vocab = set()
    for f in filters:
        nom = f.replace("NOT ", "")
        if nom in SEMANTIQUE:
            vocab |= set(_mots(SEMANTIQUE[nom]["libelle"]))
            vocab |= set(_mots(nom.replace("_", " ")))
    vocab |= {"service", "combien", "effectif", "nombre"}
    return vocab


def _termes_reellement_inconnus(decl, filters: list[str]) -> list[str]:
    """Garde les termes métier absents de la couche sémantique, jette le bruit grammatical.

    Un petit modèle met volontiers « cancer », « sein » ou « combien de » dans non_couverts :
    ce sont des mots déjà couverts ou vides. Ce qui reste — allergie, traitement, tabac — est
    exactement ce qu'on veut voir refusé.
    """
    vocab = _vocabulaire(filters)
    restants = []
    for x in decl:
        for w in _mots(str(x)):
            if len(w) <= 2 or w in MOTS_VIDES or w in vocab or w.isdigit():
                continue
            if w not in restants:
                restants.append(w)
    return restants


def valider(plan: dict) -> list[str]:
    erreurs = []
    if not plan["intent"]:
        erreurs.append("intention non reconnue")
    for f in plan["filters"]:
        nom = f.replace("NOT ", "")
        if nom not in SEMANTIQUE:
            erreurs.append(f"concept absent de la couche sémantique : {nom}")
    if plan["inconnus"]:
        erreurs.append("concept(s) non couvert(s) : " + ", ".join(sorted(set(plan["inconnus"]))))
    if plan["age"] and not (0 < plan["age"][0] < plan["age"][1] < 120):
        erreurs.append("tranche d'âge incohérente")
    return erreurs


def compiler(plan: dict) -> tuple[str, list]:
    where, params = [], []
    for f in plan["filters"]:
        if f.startswith("NOT "):
            where.append("NOT " + SEMANTIQUE[f[4:]]["sql"].format(today=TODAY.isoformat()))
        else:
            where.append(SEMANTIQUE[f]["sql"].format(today=TODAY.isoformat()))
    if plan["age"]:
        if plan.get("age_ref") == "diag":
            expr = ("(CAST(strftime('%Y', (SELECT MIN(d.date_diag) FROM diagnostics d "
                    "WHERE d.patient = p.id AND d.code_cim10 LIKE 'C50%')) AS INTEGER) - p.annee_naissance)")
        else:
            expr = "(2026 - p.annee_naissance)"
        where.append(f"{expr} BETWEEN ? AND ?")
        params += plan["age"]
    if plan["service"]:
        where.append("p.service = ?")
        params.append(plan["service"])
    clauses = ("WHERE " + " AND ".join(where)) if where else ""
    if plan["intent"] == "mean_age":
        sql = f"SELECT COUNT(*) AS n, AVG(2026 - p.annee_naissance) AS age_moyen, " \
              f"SUM(CASE WHEN 2026 - p.annee_naissance IS NULL THEN 1 ELSE 0 END) AS manquants " \
              f"FROM patients p {clauses}"
    else:
        sql = f"SELECT COUNT(*) AS n FROM patients p {clauses}"
    return " ".join(sql.split()), params


# ----------------------------------------------------------------------------------------------
# 4. Vérification des nombres + test statistique
# ----------------------------------------------------------------------------------------------

def binom_two_sided(k: int, n: int, p0: float) -> float:
    """Test binomial exact, calculé en logarithme : sur une cohorte de quelques milliers de patientes,
    `math.comb(n, i) * p**i` dépasse la capacité du float et lève OverflowError."""
    if n == 0:
        return 1.0
    log_p0, log_q0 = math.log(p0), math.log1p(-p0)
    lg_n = math.lgamma(n + 1)

    def log_pmf(i: int) -> float:
        return lg_n - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * log_p0 + (n - i) * log_q0

    obs = log_pmf(k)
    total = 0.0
    for i in range(n + 1):
        lp = log_pmf(i)
        if lp <= obs + 1e-12:
            total += math.exp(lp)
    return min(1.0, total)


def verifier(text: str, autorises: set[float]) -> tuple[bool, list[str]]:
    trouves = re.findall(r"\d+(?:[.,]\d+)?", text)
    mauvais = []
    for raw in trouves:
        val = float(raw.replace(",", "."))
        if not any(abs(val - a) < 0.55 for a in autorises):
            mauvais.append(raw)
    return (not mauvais), mauvais


# ----------------------------------------------------------------------------------------------
# 5. Le cerveau : une question → une réponse traçable, ou un refus
# ----------------------------------------------------------------------------------------------

def repondre(question: str, role: str = "chercheur", halluciner: bool = False,
             mode: str = "regles", plan_force: dict | None = None) -> dict:
    t0 = time.perf_counter()
    amb = [a for a in ambiguites(question) if not a.get("equivalent")]
    if amb:
        log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": role, "question": question,
             "statut": "clarification", "ambiguites": [a["champ"] for a in amb]})
        return {"statut": "clarification", "message": amb[0]["question"],
                "options": amb[0]["options"], "ms": round((time.perf_counter() - t0) * 1000, 1)}

    if plan_force is not None:
        plan = dict(plan_force)
        # reprise de conversation : l'objet porte la question d'origine, on ne la perd pas
        plan["_question"] = plan_force.get("_question") or question
        erreurs = valider(plan)
    elif mode == "llm":
        plan = traduire_llm(question)
        erreurs = valider(plan)
    elif mode == "double":
        plan, erreurs = double_lecture(question)
        erreurs += valider(plan)
    else:
        plan = traduire(question)
        erreurs = valider(plan)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": role, "question": question,
             "mode": mode, "plan": plan}

    # RLS simulée : un chercheur ne descend pas au patient
    if re.search(r"\bnom|liste les patientes|identité|qui sont\b", question.lower()):
        erreurs.append("accès nominatif refusé pour ce rôle (RLS)")

    if erreurs:
        entry.update({"statut": "refus_plan", "raison": erreurs})
        log(entry)
        manquants = [x for x in plan.get("inconnus", []) if x not in SEMANTIQUE]
        HORS_PERIMETRE = {"pronostic", "operer", "opérer", "posologie", "traitement recommande",
                          "faut-il", "dois-je", "risque pour cette patiente"}
        if any(m in HORS_PERIMETRE for m in manquants):
            msg = ("Avis médical : hors périmètre. Je compte des cohortes ; je ne dis pas ce qu'il faut faire "
                   "pour une patiente. La décision reste au clinicien, et c'est ce qui garde l'outil du côté "
                   "de la recherche.")
        elif any("accès nominatif" in e for e in erreurs):
            msg = ("Je ne sors pas de noms : votre rôle donne accès aux effectifs, pas aux identités. "
                   "C'est la politique d'accès, elle ne dépend pas de la question.")
        elif any(e.startswith("intention") for e in erreurs):
            msg = ("Je n'ai pas compris ce qu'il faut compter. Dites-le en clair : un nombre de patientes, "
                   "un âge moyen, ou un croisement entre deux caractéristiques ?")
        elif manquants:
            msg = ("Je ne réponds pas : « " + " », « ".join(manquants[:3]) + " » n'existe pas dans la "
                   "couche sémantique. Une définition ne s'invente pas — elle s'écrit : source, périmètre, "
                   "version, validation par un clinicien. C'est exactement ce que produit la première "
                   "phase du plan, sur trente de vos questions.")
        else:
            msg = ("Je ne peux pas répondre : " + " ; ".join(erreurs) +
                   ". Je préfère le dire que d'inventer un chiffre.")
        return {"statut": "refus", "raison": erreurs, "plan": plan, "message": msg,
                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    # Un compte sans aucun critère n'est pas une réponse : c'est « toutes les patientes »,
    # déguisé en chiffre vérifié. C'est exactement le mode de défaillance qu'on interdit.
    if plan["intent"] in ("count", "mean_age") and not plan["filters"] and not plan["age"] and not plan["service"]:
        msg = ("Votre question ne contient aucun critère que je sache traduire. Répondre « toutes les "
               "patientes » serait un chiffre vrai et une réponse fausse. Précisez le critère — pathologie, "
               "âge, service, période — et je compte.")
        entry.update({"statut": "refus_sans_critere"})
        log(entry)
        return {"statut": "refus", "raison": [msg], "plan": plan, "message": msg,
                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    injustifies = justifier(plan, plan.get("_question") or question)
    if injustifies:
        msg = ("Le plan ajoute un filtre que votre question ne justifie pas : « "
               + " », « ".join(injustifies) + " ». Je ne réponds pas à une question que vous n'avez "
               "pas posée — reformulez si ce filtre compte vraiment.")
        entry.update({"statut": "refus_justification", "injustifies": injustifies})
        log(entry)
        return {"statut": "refus", "raison": [msg], "plan": plan, "message": msg,
                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    sql, params = compiler(plan)
    con = connect()
    row = dict(con.execute(sql, params).fetchone())
    con.close()
    n = row.get("n") or 0

    # Le cas de David : « elles ont toutes l'expression du gène X, est-ce qu'on creuse ? »
    # On sépare la cohorte (le groupe) de l'hypothèse (le gène), sinon on compte deux fois.
    base_filters = [f for f in plan["filters"] if f != "expression_gene_x"]
    if "expression_gene_x" in plan["filters"]:
        base_plan = dict(plan); base_plan["filters"] = base_filters
        sql, params = compiler(base_plan)
        con = connect(); n = con.execute(sql, params).fetchone()["n"]; con.close()

    entry.update({"statut": "ok", "sql": sql, "params": params, "resultat": {"n": n}})

    if n < SEUIL_N:
        entry.update({"statut": "refus_effectif"})
        log(entry)
        return {"statut": "refus", "n": n, "plan": plan, "sql": sql, "params": params,
                "message": (f"Moins de {SEUIL_N} patientes correspondent à cette définition. À ce niveau, "
                            "donner le chiffre reviendrait à désigner des personnes : je m'arrête là et je le "
                            "signale. On peut élargir la tranche d'âge ou retirer un critère — mais c'est "
                            "votre décision, pas la mienne."),
                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def fr(x: float, nd: int = 1) -> str:
        return f"{x:.{nd}f}".replace(".", ",")

    autorises: set[float] = {float(n)}
    if plan["intent"] == "mean_age":
        age = row.get("age_moyen") or 0
        manquants = row.get("manquants") or 0
        calculees = n - manquants
        autorises.update({round(age, 1), round(age), float(manquants), float(calculees)})
        texte = (f"Âge moyen : {fr(age)} ans, calculé sur {calculees} patientes — "
                 f"{manquants} sans année de naissance connue sont exclues du calcul, pas comptées zéro.")
        resultat = {"n": n, "age_moyen": round(age, 1), "sans_annee_naissance": manquants}
    else:
        texte = f"{n} patientes correspondent à la définition."
        resultat = {"n": n}

    # l'exemple de David : gène X + petit effectif → test, et verdict « non conclusif »
    if "expression_gene_x" in plan["filters"] and n >= SEUIL_N:
        pos_plan = dict(plan); pos_plan["filters"] = base_filters + ["expression_gene_x"]
        sql_pos, params_pos = compiler(pos_plan)
        con = connect()
        pos = con.execute(sql_pos, params_pos).fetchone()["n"]
        con.close()
        pval = binom_two_sided(pos, n, REF_FREQ)
        autorises.update({float(pos), round(100 * pos / n), round(pval, 3), round(pval, 2),
                          round(REF_FREQ * 100), 0.001})
        if pval < 0.001:
            p_txt, autorises_p = "p < 0,001", "p < 0,001"
        else:
            p_txt = "p = " + f"{pval:.3f}".replace(".", ",")
            autorises_p = p_txt
        texte = (f"{pos} patientes sur {n} expriment le gène X ({100 * pos / n:.0f} %), contre "
                 f"{REF_FREQ * 100:.0f} % attendus dans la cohorte de référence. "
                 f"{p_txt} — n = {n}{', effectif faible' if n < 20 else ''} : signal à investiguer, non conclusif.")
        resultat.update({"positives": pos, "frequence_pct": round(100 * pos / n), "p_value": round(pval, 3)})

    if halluciner:                       # bouton « simuler une hallucination »
        texte = texte.replace(str(n), str(n + 7), 1)

    ok, mauvais = verifier(texte, autorises)
    entry.update({"statut": "verifie" if ok else "rejete", "nombres_non_traces": mauvais})
    log(entry)
    if not ok:
        return {"statut": "rejete", "plan": plan, "sql": sql, "params": params,
                "message": "Réponse rejetée : un nombre du texte n'existe pas dans le résultat "
                           f"({', '.join(mauvais)}). Le système régénère au lieu d'afficher.",
                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    # « et ensuite ? » — la suggestion fait partie du contrat, pas de la décoration
    suite = []
    if plan["age"]:
        bas, haut = plan["age"]
        suite.append(f"élargir la tranche d'âge à {max(0, bas - 5)}–{haut + 5} ans")
    if "expression_gene_x" in plan["filters"]:
        suite.append("comparer cette fréquence à la cohorte de référence complète")
    if plan["service"]:
        suite.append("regarder la même question dans les autres services")
    suite += ["enregistrer cette cohorte comme actif réutilisable",
              "exporter les effectifs pour revue clinique"]

    return {"statut": "verifie", "plan": plan, "sql": sql, "params": params, "resultat": resultat,
            "message": texte, "suite": suite[:3],
            "definitions": {f.replace("NOT ", ""): SEMANTIQUE[f.replace("NOT ", "")]
                            for f in plan["filters"]},
            "date_reference": TODAY.isoformat(), "seuil": SEUIL_N,
            "ms": round((time.perf_counter() - t0) * 1000, 1)}


def log(entry: dict) -> None:
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__" and "--ask" in sys.argv:
    build_db()
    q = sys.argv[sys.argv.index("--ask") + 1]
    print(json.dumps(repondre(q), ensure_ascii=False, indent=1))
    raise SystemExit(0)
