# Quatre modes, et ce qui en est fait

Une page pour la conversation : ce que le chat sait faire aujourd'hui, ce qu'il refuse encore, et pourquoi.

## Le principe

Un chat qui répond à tout est un chat qui invente. Un chat qui refuse tout n'est pas un chat. La sortie que je
propose tient en quatre modes, et le mode est décidé par la question, pas par l'humeur du modèle.

| Mode | Question type | Qui répond | Ce qui est garanti | État |
|---|---|---|---|---|
| **1. Nombre vérifié** | « combien de patientes… ? » couvert par le catalogue | la chaîne : plan → validation → SQL → contrôle | chiffre, requête, définitions, journal, bouton Rejouer | **fait** |
| **4. Conversation** | « qu'est-ce que patiente active ? », « quels concepts ? », « explique ce chiffre » | le catalogue et le dernier résultat, en texte | aucune invention : la réponse est lue dans un objet daté | **fait** |
| **3. Documents** | « que disent les comptes rendus… ? » | recherche dans les rapports + citation | rappel mesuré à 88,2 %, citation par document | mesuré, **non branché** |
| **2. Nombre non validé** | concept absent du catalogue | modèle + SQL proposé, réponse **étiquetée** | export bloqué, mention « non validé », SQL visible | **désactivé**, voir plus bas |

Le refus dur reste réservé à deux cas, et il ne bouge pas : **avis médical sur une patiente identifiée**, et
**sortie d'identités**.

## Ce qui fait la différence entre un chat et un formulaire

**La cohorte devient un objet.** Après une réponse vérifiée, la conversation retient le plan, l'effectif et
une étiquette lisible. On peut alors enchaîner :

> « Combien de patientes ont un cancer du sein confirmé entre 25 et 36 ans, sans grossesse antérieure ? »
> → 16
> « et parmi elles plus de 30 ans ? » → 10
> « combien d'entre elles expriment le gène X ? » → 8 sur 10, 80 %, contre 35 % attendus
> « et si on retire le critère grossesse ? » → 11 sur 29, 38 %

Trois choses à noter dans cette séquence. « Plus de 30 ans » **rétrécit** la tranche existante au lieu de la
remplacer (16 → 10, pas 211). « Retirer un critère » le **sort** du plan au lieu de le réajouter parce que le
mot est cité (10 → 29, vérifié par une requête directe). Et chaque étape garde sa preuve : même plan, même
journal, même bouton.

**Le refus propose une sortie.** Au lieu de « je n'ai pas compris », la réponse nomme ce qui manque et
propose la question la plus proche que le système sait traiter — un clic suffit à l'envoyer.

**Un refus peut être juste.** « et au service C ? » sur la cohorte ci-dessus donne n = 7 : sous le seuil de
restitution, le système s'arrête et le dit. Ce n'est pas un échec du chat, c'est la règle qui protège les
personnes.

## Ce qui reste éteint, et pourquoi

**Mode 2 (nombre non validé).** Il rendrait service — la majorité des 26 refus du carnet sont des concepts
absents du catalogue. Mais un chiffre non validé exige des contrôles indépendants : second modèle, accord
d'exécution, comparaison au référentiel. Mesure du jour : un 4 B sous schéma imposé rend 5 chiffres, dont
**4 faux**, sur ces mêmes quarante questions. Tant que ce contrôle n'existe pas, activer le mode 2 revient à
mettre la panne au milieu du produit.

**Mode 3 (documents).** Le rappel est mesuré (88,2 % sur 200 comptes rendus), le chemin n'est pas branché :
c'est une phase de quatre semaines, pas une case à cocher.

## Ce que la mesure du jour a changé

- carnet de 40 questions : **4 nombres vérifiés, 9 demandes de précision, 26 refus, 1 réponse de
  conversation** (contre 27 refus avant : une question de définition est maintenant servie par le catalogue) ;
- un bug réel trouvé en écrivant la continuité : le test binomial dépassait la capacité du `float` sur une
  cohorte de plusieurs milliers de patientes (`OverflowError`). Corrigé, calcul en logarithme ;
- un second bug : « retirer un critère » le réajoutait, parce que le mot cité déclenchait son propre concept.
  Corrigé, et vérifié par une requête indépendante.

Ces deux bugs n'auraient pas été trouvés par les quarante questions : il a fallu **parler** au système pour
les faire apparaître. C'est l'argument en faveur de la couche conversationnelle.
