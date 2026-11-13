"""Couche conversationnelle : quatre modes, mémoire de cohorte, refus qui propose une sortie.

Ce module ne touche pas au cœur (traduction, validation, compilation) : il décide **qui répond** à une
question, et garde le fil d'une conversation. Trois modes sont actifs ici :

  1. vérifié      — la question est couverte par le catalogue : la chaîne habituelle répond, avec sa preuve ;
  2. conversation — « quels concepts ? », « qu'est-ce que patiente active ? », « explique ce chiffre » :
                    réponse déterministe, tirée du catalogue et du dernier résultat, jamais inventée ;
  3. refus utile  — la question sort du périmètre : le refus nomme ce qui manque **et propose** la question
                    la plus proche que le système sait traiter.

Les deux autres modes (nombre non validé, réponse documentaire) sont décrits dans la note et restent
désactivés : le premier exige une vérification indépendante, le second n'est pas branché.
"""
from __future__ import annotations

import re
import time

import brain

# ------------------------------------------------------------------------------------------------
# Mémoire de conversation : une cohorte nommée, et ce qu'on peut en dire ensuite
# ------------------------------------------------------------------------------------------------

SESSIONS: dict[str, dict] = {}


def nouvelle_session() -> str:
    sid = f"s{int(time.time() * 1000) % 10**9}"
    SESSIONS[sid] = {}
    return sid


def _etiquette(plan: dict) -> str:
    morceaux = []
    for f in plan.get("filters", []):
        nom = f.replace("NOT ", "")
        neg = "sans " if f.startswith("NOT ") else ""
        morceaux.append(neg + nom.replace("_", " "))
    if plan.get("age"):
        morceaux.append(f"{plan['age'][0]}–{plan['age'][1]} ans")
    if plan.get("service"):
        morceaux.append(f"service {plan['service']}")
    return ", ".join(morceaux) or "toutes les patientes"


def _memoriser(sid: str, plan: dict, n: int) -> None:
    if not sid:
        return
    SESSIONS.setdefault(sid, {})
    SESSIONS[sid].update({"plan": dict(plan),  # on garde la question d'origine pour justifier la suite
                          "n": n, "label": _etiquette(plan),
                          "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})


# ------------------------------------------------------------------------------------------------
# Continuité : « et parmi elles plus de 30 ans ? », « et au service C ? »
# ------------------------------------------------------------------------------------------------

SUITE = re.compile(r"^(et|parmi|sinon|maintenant|et si|et pour|et dans|compare)", re.I)


def est_suite(question: str) -> bool:
    t = question.strip()
    if re.search(r"d'entre elles|de ces patientes|dans cette cohorte|parmi ces|dedans", t, re.I):
        return True
    return bool(SUITE.search(t)) and len(t.split()) <= 14


def appliquer_suite(question: str, session: dict) -> dict | None:
    """Fusionne la nouvelle contrainte dans le plan mémorisé. Renvoie un plan, ou None si on ne sait pas."""
    plan = session.get("plan")
    if not plan:
        return None
    t = question.lower()
    nouveau = {k: (list(v) if isinstance(v, list) else v) for k, v in plan.items()}
    nouveau["inconnus"] = []
    nouveau["_question"] = plan.get("_question", "") + " " + question
    touche = False

    ancien = plan.get("age") or [1, 119]
    if re.search(r"(plus de|à partir de|apres|après) (\d{1,3}) ans", t):
        # « parmi elles plus de 30 ans » = intersection avec la tranche courante, pas remplacement
        nouveau["age"] = [max(ancien[0], int(re.search(r"(\d{1,3}) ans", t).group(1))), ancien[1]]
        touche = True
    elif re.search(r"(moins de|jusqu'à) (\d{1,3}) ans", t):
        nouveau["age"] = [ancien[0], min(ancien[1], int(re.search(r"(\d{1,3}) ans", t).group(1)))]
        touche = True
    if re.search(r"service ([a-d])\b", t):
        nouveau["service"] = re.search(r"service ([a-d])\b", t).group(1).upper()
        touche = True
    # retrait explicite d'un critère : on le sort, et on n'ajoute surtout pas le concept cité
    retrait = re.search(r"(retire|enlève|enleve|supprime|sans) (le |la |les )?(critère |critere |filtre )?", t)
    if retrait and re.search(r"critère|critere|filtre|grossesse|gène|gene|hospitalis|active", t):
        cible = None
        for mot, concept in (("gène x", "expression_gene_x"), ("gene x", "expression_gene_x"),
                             ("grossesse", "grossesse_anterieure"),
                             ("hospitalis", "hospitalisation_recente"), ("active", "patiente_active")):
            if mot in t:
                cible = concept
                break
        if cible:
            nouveau["filters"] = [f for f in nouveau["filters"] if f.replace("NOT ", "") != cible]
        else:
            nouveau["filters"] = nouveau["filters"][:-1]
        return nouveau

    for mot, concept in (("gène x", "expression_gene_x"), ("gene x", "expression_gene_x"),
                         ("grossesse", "grossesse_anterieure"), ("hospitalis", "hospitalisation_recente"),
                         ("active", "patiente_active")):
        if mot in t and concept not in nouveau["filters"]:
            nouveau["filters"] = nouveau["filters"] + [concept]
            touche = True
    return nouveau if touche else None


# ------------------------------------------------------------------------------------------------
# Mode conversation : définitions, catalogue, explication du dernier chiffre
# ------------------------------------------------------------------------------------------------

def _concept_par_mot(q: str) -> str | None:
    t = q.lower()
    meilleur, score = None, 0
    for nom, c in brain.SEMANTIQUE.items():
        for d in c.get("declencheurs", []):
            if d and d in t and len(d) > score:
                meilleur, score = nom, len(d)
    return meilleur


def reponse_conversationnelle(question: str, session: dict) -> dict | None:
    t = question.lower().strip()

    if re.search(r"quels concepts|quelles définitions|quelles definitions|catalogue|que sais-tu|que savez-vous", t):
        lignes = [f"- **{c['libelle']}** ({nom} · {c['version']} · validé par {c['valide_par']})"
                  for nom, c in brain.SEMANTIQUE.items()]
        return {"statut": "conversation", "mode": "4",
                "message": "Aujourd'hui, le catalogue contient neuf définitions :\n" + "\n".join(lignes),
                "concepts": list(brain.SEMANTIQUE)}

    if re.search(r"^(qu'est-ce que|qu est ce que|c'est quoi|définition de|definition de|explique (le|la|les) concept|à quoi correspond|a quoi correspond)", t):
        nom = _concept_par_mot(q=t)
        if nom:
            return {"statut": "conversation", "mode": "4", "concept": nom,
                    "message": (f"**{brain.SEMANTIQUE[nom]['libelle']}**\n"
                                f"Version {brain.SEMANTIQUE[nom]['version']}, source {brain.SEMANTIQUE[nom]['source']}, "
                                f"validé par {brain.SEMANTIQUE[nom]['valide_par']}.\n"
                                f"Règle appliquée : {brain.SEMANTIQUE[nom]['sql'].strip()}"),
                    "definition": brain.SEMANTIQUE[nom]}

    if re.search(r"explique|d'où vient|d ou vient|justifie|pourquoi ce chiffre|recalcul", t) and session.get("plan"):
        plan = session["plan"]
        return {"statut": "conversation", "mode": "4",
                "message": (f"Le chiffre {session['n']} vient de la cohorte « {session['label']} ». "
                            f"Plan : {plan.get('filters')}, âge {plan.get('age')}, service {plan.get('service')}. "
                            "Le bouton « Rejouer » refait la requête et compare le résultat."),
                "plan": plan, "n": session["n"]}

    if re.search(r"^(bonjour|salut|merci|au revoir|ça va|ca va)", t):
        return {"statut": "conversation", "mode": "4",
                "message": ("Je réponds sur les cohortes de cette base : effectifs, âges moyens, croisements, "
                            "et des définitions quand vous en doutez. Demandez « quels concepts ? » ou "
                            "« combien de patientes ont un cancer du sein confirmé ? ».")}
    return None


# ------------------------------------------------------------------------------------------------
# Refus utile : nommer ce qui manque, puis proposer la question la plus proche
# ------------------------------------------------------------------------------------------------

def suggestions(question: str, plan: dict | None = None, inconnus: list[str] | None = None) -> list[str]:
    t = question.lower()
    out = []
    concept = _concept_par_mot(q=t)
    if concept:
        out.append(f"Combien de patientes ont {brain.SEMANTIQUE[concept]['declencheurs'][0]} ?")
        out.append(f"Quel est l'âge moyen des patientes avec {brain.SEMANTIQUE[concept]['declencheurs'][0]} ?")
    for mot in (inconnus or [])[:1]:
        proche = next((n for n in brain.SEMANTIQUE if mot[:4] in n), None)
        if proche:
            out.append(f"Combien de patientes ont {brain.SEMANTIQUE[proche]['declencheurs'][0]} ?")
    if not out:
        out = ["Combien de patientes ont un cancer du sein confirmé ?",
               "Combien de patientes ont eu une grossesse ?",
               "Quels concepts sont disponibles ?"]
    vus, uniques = set(), []
    for s in out:
        if s not in vus:
            vus.add(s); uniques.append(s)
    return uniques[:3]


# ------------------------------------------------------------------------------------------------
# Point d'entrée : choisit le mode, garde le fil, et ne laisse jamais un refus sans issue
# ------------------------------------------------------------------------------------------------

def traiter(question: str, role: str = "chercheur", halluciner: bool = False,
            mode: str = "regles", sid: str = "") -> dict:
    sid = sid or nouvelle_session()
    SESSIONS.setdefault(sid, {})
    session = SESSIONS[sid]

    # mode 2 (nombre non validé) et mode 3 (documents) : désactivés, et on le dit plutôt que de faire semblant
    if mode in ("libre", "documents"):
        return {"statut": "refus", "mode": "2" if mode == "libre" else "3", "sid": sid,
                "message": ("Ce mode n'est pas actif dans le démonstrateur : un chiffre non validé exige des "
                            "contrôles indépendants que je n'ai pas encore, et la recherche documentaire n'est "
                            "pas branchée. Je préfère le dire que le simuler."),
                "suggestions": suggestions(question)}

    # 1) continuité de la conversation
    plan_suite = appliquer_suite(question, session) if est_suite(question) else None
    question_cumulee = question
    if plan_suite is not None:
        question_cumulee = plan_suite.get("_question") or question
        res = brain.repondre(question, role, halluciner, mode, plan_force=plan_suite)
        res["mode"] = "1"
        res["suite_de"] = session.get("label")
    else:
        # 2) conversation (définitions, catalogue, explication)
        conv = reponse_conversationnelle(question, session)
        if conv is not None:
            conv["sid"] = sid
            return conv
        # 3) chaîne vérifiée
        res = brain.repondre(question, role, halluciner, mode)
        res["mode"] = "1"

    if res.get("statut") == "verifie":
        plan_memo = dict(res.get("plan") or {})
        plan_memo["_question"] = question_cumulee  # la question accumulée, sinon la suite ne peut pas se justifier
        _memoriser(sid, plan_memo, (res.get("resultat") or {}).get("n") or 0)
    elif res.get("statut") == "refus":
        res["suggestions"] = suggestions(question, res.get("plan"),
                                         (res.get("plan") or {}).get("inconnus", []))
    res["sid"] = sid
    if session.get("n"):
        res["cohorte_courante"] = {"n": session["n"], "label": session.get("label")}
    return res
