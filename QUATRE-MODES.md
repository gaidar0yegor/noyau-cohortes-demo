# Quatre modes, et ce qui en est fait

Une page pour la conversation : ce que le chat sait faire aujourd'hui, ce qu'il refuse, et pourquoi.

## Le principe

Un chat qui répond à tout invente. Un chat qui refuse tout n'est pas un chat. La sortie tient en quatre modes,
et le mode est décidé par la question, pas par l'humeur d'un modèle.

| Mode | Question type | Qui répond | Ce qui est garanti | État |
|---|---|---|---|---|
| **1. Nombre vérifié** | « combien de patientes… ? » couvert par le catalogue | la chaîne : plan → validation → SQL → contrôle | chiffre, requête, définitions, journal, bouton Rejouer | **fait** |
| **4. Conversation** | « qu'est-ce que patiente active ? », « quels concepts ? », « explique ce chiffre » | le catalogue et le dernier résultat | aucune invention : la réponse est lue dans un objet daté | **fait** |
| **3. Documents** | « que disent les comptes rendus… ? » | recherche + citation | rappel mesuré à 88,2 %, citation par document | mesuré, **non branché** |
| **2. Nombre non validé** | concept absent du catalogue | modèle + SQL proposé, réponse **étiquetée** | export bloqué, mention « non validé » | **désactivé**, voir plus bas |

Le refus dur reste réservé à deux cas : **avis médical sur une patiente identifiée**, et **sortie d'identités**.

## La conversation, pas le formulaire

La séquence ci-dessous est celle que le démonstrateur produit réellement, avec ses étapes d'incertitude.

> **1.** « Combien de patientes ont un cancer du sein entre 25 et 36 ans, sans grossesse antérieure ? »
> → *Avec la validation anatomopathologique : 16 patientes. Sans elle : 51. Laquelle des deux définitions ?*
> — puis, une fois la définition choisie, la référence d'âge est demandée à son tour (**29** au diagnostic,
> **16** aujourd'hui). Le 16 qui suit est celui d'aujourd'hui, après ces deux choix.
>
> **2.** « et parmi elles plus de 30 ans ? » → **10**
> `… AND NOT EXISTS (grossesses) AND (2026 - annee_naissance) BETWEEN 30 AND 36` — la tranche est
> **rétrécie**, pas remplacée.
>
> **3.** « combien d'entre elles expriment le gène X ? » → **8 sur 10 (80 %)**, contre 35 % attendus,
> p = 0,005, avec la mention **« effectif faible : signal à investiguer, non conclusif »** (n < 20).
>
> **4.** « et si on retire le critère grossesse ? » → **29**, mais ce n'est pas le 29 de l'étape 1 :
> `… WHERE C50 confirmé AND (2026 - annee_naissance) BETWEEN 30 AND 36` — ici, c'est le nombre de patientes
> de 30 à 36 ans quand on cesse d'exiger l'absence de grossesse ; là-bas, c'était l'âge **à la date du
> diagnostic** sur la cohorte initiale. Deux questions, deux requêtes, que le journal sépare.

Trois comportements à retenir. « Plus de 30 ans » rétrécit la tranche au lieu de la remplacer (10, pas 211).
« Retirer un critère » le sort du plan au lieu de le réajouter parce que le mot est cité. Et chaque étape
garde sa preuve : plan, SQL, journal, bouton Rejouer.

**Le refus propose une sortie.** Au lieu de « je n'ai pas compris », la réponse nomme ce qui manque et propose
la question la plus proche que le système sait traiter ; un clic l'envoie.

**Un refus peut être juste, et il ne divulgue rien.** « et au service C ? » sur cette cohorte tombe sous le
seuil de restitution : le système s'arrête et **n'affiche pas l'effectif** — la cellule est trop petite, et la
dire reviendrait à désigner des personnes.

## Ce qui reste éteint, et pourquoi

**Mode 2 (nombre non validé).** Il rendrait service : la majorité des 26 refus du carnet sont des concepts
absents du catalogue. Mais un chiffre non validé exige des contrôles indépendants — second modèle, accord
d'exécution, comparaison au référentiel. Mesure du jour : un 4 B sous schéma imposé rend 5 chiffres, dont
**4 faux**, sur ces mêmes questions. Tant que ce contrôle n'existe pas, l'activer installe la panne au milieu
du produit.

**Mode 3 (documents).** Rappel mesuré (88,2 % sur 200 comptes rendus), chemin non branché : quatre semaines de
travail, pas une case à cocher.

## Ce que la journée a changé

- carnet de 40 questions : **4 nombres vérifiés, 9 demandes de précision, 26 refus, 1 réponse de conversation**
  (contre 27 refus auparavant : une question de définition est maintenant servie par le catalogue).
  L'état correspondant à la note envoyée porte le tag `note-2026-11-12` du dépôt ;
- **deux bugs trouvés en parlant au système**, pas en posant les 40 questions : le test binomial dépassait la
  capacité du `float` sur une cohorte de plusieurs milliers de patientes (`OverflowError`), et « retirer un
  critère » le réajoutait parce que le mot cité déclenchait son propre concept ;
- un troisième, trouvé en écrivant cette page : le refus par petit effectif affichait le chiffre qu'il était
  censé taire. Corrigé, et vérifié.
