# Noyau IA pour cohortes hospitalières — démonstrateur

Démonstrateur d'un noyau qui répond à des questions de cohorte sur une base hospitalière fermée, avec la
requête qui justifie chaque chiffre, un refus explicite hors périmètre, et **deux choix rendus visibles
plutôt qu'un choix silencieux**.

> **Données 100 % synthétiques.** 3 000 patientes générées avec une graine fixe (`build_db()` dans
> `app/brain.py`). Aucune donnée patient, aucune donnée réelle, aucune donnée de client.

## Lancer en une commande

```bash
docker compose up
```

Puis ouvrir <http://localhost:8090>. (Le port d'écoute interne reste 8099 ; 8090 côté hôte évite les collisions.)

Aucune carte graphique n'est nécessaire : le démonstrateur tourne en mode **règles** (traducteur
déterministe + catalogue sémantique). Le mode « LLM » existe mais reste optionnel — il interroge un serveur
Ollama local (`qwen3:1.7b` par défaut) et ne change ni la base ni les garanties.

Sans Docker :

```bash
cd app && pip install pyyaml && python3 app.py
```

## Ce que la démonstration montre

| Question | Ce qui se passe |
|---|---|
| « Combien de patientes ont un cancer du sein entre 25 et 36 ans, sans grossesse antérieure ? » | le système exécute les deux définitions du catalogue et affiche **16 ou 51**, puis demande |
| la même, après choix de la définition | il demande la **référence d'âge** : **29** au diagnostic ou **16** aujourd'hui |
| « Combien de patientes ont un allergie aux arachides ? » | refus explicite : le concept n'existe pas dans la couche sémantique |
| « Quel est le pronostic de cette patiente ? » | refus : avis médical, hors périmètre |
| « Donne-moi le nom des patientes » | refus : le rôle donne accès aux effectifs, pas aux identités |
| bouton « Rejouer » | rejoue la même question et compare le chiffre |

Chaque réponse vérifiée contient : le plan lisible, la requête SQL compilée, le chiffre, les définitions
employées avec leur version et leur validateur, et une suite proposée.

## Ce que contient le dépôt

```
app/         démonstrateur (stdlib + PyYAML), base synthétique, catalogue sémantique
questions/   les 40 questions du carnet, et leur journal d'exécution
bench/       scripts de mesure et résultats bruts
```

- `questions/QUESTIONS-MEDECINS.md` — quarante questions écrites par un profil utilisateur (oncologue,
  épidémiologiste, attaché de recherche clinique, data manager, cadre), classées par ce qui doit y répondre.
- `questions/questions_medecins.json` — journal réel : **5 chiffres, 8 demandes de précision, 27 refus,
  0 erreur interne**.
- `bench/bench_ladder.py` — échelle de concurrence sur une H100, contextes de 8 k uniques.
  Résultat brut : `bench/ladder.raw` (24 sessions, TTFT p95 347 ms, 0 erreur).
- `bench/bench_extract.py` — extraction de 200 comptes rendus en JSON contraint par schéma.
  Résultat brut : `bench/extract.raw` (200/200 valides, 10,4 s, ≈69 200 documents/heure).
- `bench/bench_sql_direct.py` + `sql40.json` + `sql40_eval.json` — comparaison text-to-SQL contre la chaîne
  sur les mêmes quarante questions : 0/5 et 0/5 de réponses justes pour les deux modèles testés, et un
  chiffre rendu 3 et 8 fois là où la chaîne refuse.

## Le garde-fou, en une phrase

Le traducteur vérifie que **chaque filtre du plan vient de la question** et que **chaque mot de la question
a un filtre** ; un mot non couvert produit un refus nommé, pas une estimation. Un plan sans aucun critère
n'est pas une réponse : c'est un refus.

## Limites, écrites avant qu'on les découvre

- Le carnet de 40 questions est **biaisé** : neuf pièges sur quarante, il ne représente pas une charge réelle.
- Le chemin documentaire (recherche dans les comptes rendus, citations) n'est **pas branché** dans ce
  démonstrateur ; la mesure de rappel (88,2 %) existe, hors démonstrateur.
- Aucune mesure sur données réelles, français médical compris.
- Les chiffres de dimensionnement viennent d'une seule campagne sur 4×H100 ; les chiffres de latence sur une
  seule H100.

## Licences des modèles cités

| Modèle | Licence | Usage dans ce dépôt |
|---|---|---|
| Qwen2.5-3B | **Qwen Research License — évaluation seulement** | utilisé pour des mesures d'évaluation, jamais en production |
| Qwen3-30B-A3B, Qwen3-4B | Apache 2.0 | référence des mesures de débit |
| Mistral Small 3.2 24B | Apache 2.0 | alternative non mesurée ici |
| DeepSeek-V4-Flash | MIT | modèle de comparaison text-to-SQL |

Le code de ce dépôt est un démonstrateur de méthode : il n'est pas destiné à un usage clinique.

## Reproductibilité

```bash
# reconstruire la base synthétique
cd app && python3 -c "import brain; brain.build_db()"

# rejouer le carnet sur la chaîne
python3 - <<'PY'
import json, urllib.request, pathlib
bank = json.loads(pathlib.Path("questions/questions_medecins.json").read_text())
for q in bank[:5]:
    r = urllib.request.Request("http://localhost:8090/api/ask",
        data=json.dumps({"q": q["q"], "role": "chercheur", "mode": "regles"}).encode(),
        headers={"Content-Type": "application/json"})
    print(json.loads(urllib.request.urlopen(r).read())["statut"], q["q"][:60])
PY
```
