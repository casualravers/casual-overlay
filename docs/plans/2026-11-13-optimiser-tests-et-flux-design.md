# Optimiser les tests et le flux de developpement : spec

Issue #121 (« Optimiser les tests ») elargie par la demande : « reduire le temps de dev, tout en garantissant la qualite ».
Statut : **a valider** (aucune ligne de code ecrite). Plan d'execution : `2026-11-13-optimiser-tests-et-flux-plan.md`.

## 1. Ce que je comprends

- **But** : qu'un changement passe de « code fini » a « merge » en bien moins de temps, sans qu'une regression qui aurait ete vue avant le soit apres.
- **Pas le but** : supprimer des tests pour aller vite, ou assouplir un seuil pour qu'un test passe.
- **Succes** : budgets mesures (section 5), et pour chaque test allege ou retire, la preuve qu'il echoue toujours quand on casse ce qu'il garde.

## 2. Mesures (journal du run vert sur `main` du 2026-11-12, `complet`)

Suite complete **24 min** (runs recents : 16 a 26 min ; avant le labyrinthe : ~12 min). `pur` 1 min, `portable` 1 min.
Les plus longues verifications (secondes) : `check_reaction_sim` 228, `check_bg_labyrinthe` 192, `check_gui` 166, `check_foudre_render` 119, `check_rd_zones` 95,
`check_bg_stochastic` 91, `check_rd_symmetry` 75, `check_render` 62, `check_masters` 58, `check_doodles` 45, `check_bench_kick` 43, `check_holo` 40, `check_foudre_pure` 36, `check_sphere_mode` 29.

Ce que ces chiffres disent :

1. **La famille labyrinthe pese ~590 s sur ~1440 (41 %)**. Cout mesure : ~33 ns par cellule et par pas de simulation sur llvmpipe (~30 millions de cellules-pas par seconde) ;
   `check_reaction_sim` fait ~6 milliards de cellules-pas, `check_rd_zones` 3,3, `check_rd_symmetry` 2,1, `check_bg_labyrinthe` 2,2 (+ le rendu des images).
2. **Du travail est fait deux fois** : les verifications `PURES` tournent dans `pur` puis encore dans `complet` (`check_bench_kick` 43 s, `check_foudre_pure` 36 s, etc.) ;
   et un echec de mise en page « fragile » relance **toute** la suite (+24 min) au lieu du seul test fautif.
3. **Un seul processus, un seul coeur utile** : la suite est sequentielle, alors que la plupart des familles (moteur GL, labyrinthe, foudre) sont independantes de la GUI.
4. **Le CI tourne aussi sur chaque push de `main`** (meme contenu que la PR deja verte), et une PR en retard sur `main` repart a zero apres `update-branch`.
5. **Cout facture (a verifier dans Settings > Billing)** : le depot est prive ; si le forfait est gratuit (2000 minutes par mois, minutes Windows comptees double), un run de ~28 minutes
   en vaut ~56 ; une PR typique (2 pushes + 1 run sur `main`) en consomme ~170 : de l'ordre de 12 PR par mois. C'est un risque de blocage, pas seulement de confort.
6. **Le flux autour des tests coute aussi** : le dossier de travail est partage entre sessions (changements de branche subis, tests de l'une qui lisent le code de l'autre, vu pendant la PR #117),
   et chaque merge demande de surveiller le CI a la main.

## 3. Principes (non negociables)

- **Aucun test supprime ni allege sans fiche** : ce qu'il garantit, comment c'est preserve (ou pourquoi ce n'est plus utile), et son **test de sensibilite**.
- **Test de sensibilite** : on injecte volontairement le defaut que le test doit voir (changer une constante, casser une branche), sur l'ancienne ET la nouvelle version du test : les deux doivent echouer.
  Un test allege qui ne voit plus le defaut est refuse. Fait une fois, a la main, note dans la fiche.
- **Les seuils ne bougent pas pour passer** : on reduit la donnee (pas, taille de grille, images), on partage un calcul entre assertions, jamais on ne desserre une borne. (Appris pendant la PR #117 : passer
  a une grille 1,5 fois plus petite faisait echouer trois seuils de `check_rd_zones` et `check_rd_symmetry` ; ces mesures restent donc a pleine grille.)
- **La suite complete tourne toujours avant un merge dans `main`** ; ce qui change, c'est comment (en parallele, sans doublon), pas si.
- **Le budget est un test** : les durees sont imprimees a chaque run, et une verification qui depasse son budget le dit (information d'abord, bloquant seulement apres validation).

## 4. Leviers (chacun avec son gain attendu et son risque)

| # | Levier | Gain attendu | Risque / garde-fou |
|---|---|---|---|
| A | **Mesure permanente** : `--duree`, tableau des durees dans le resume du job, fiche par verification (garde / alleger / fusionner / retirer) | rien en soi ; rend le reste verifiable | aucun |
| B | **Parallelisme** : `complet` coupe en 3 groupes independants (moteur GL, GUI, reste) en matrice ; `check_gl.py --groupe` ; un test verifie que chaque `check_*` est dans **exactement un** groupe | temps reel 24 -> ~12 min | piege connu : la partie GUI depend de l'etat laisse par des verifications d'avant : le groupe GUI garde ses prerequis ; chaque groupe est lance seul en local avant fusion. Minutes facturees inchangees (somme) |
| C | **Alleger les tests lourds** (labyrinthe d'abord) : reutiliser une meme simulation pour plusieurs assertions (`check_rd_zones` en refait 5 de 6000 pas, `check_rd_symmetry` 3), moins d'images rendues, rendu a 320 x 180 quand le pixel exact n'importe pas | -590 s -> ~250 s | test de sensibilite par fiche ; seuils intacts |
| D | **Plus de doublons** : `complet` saute les `PURES` deja passees par `pur` (`--sans-pur`) ; un echec fragile ne relance **que** les verifications fragiles echouees (`--seulement`) | -80 a -100 s par run ; -24 min les jours de mise en page instable | `pur` reste bloquant avant `complet` (deja le cas : `needs: pur`) |
| E | **Ne pas relancer `complet` sur chaque push de `main`** : PR = suite complete ; `main` = `pur` + `portable` ; `complet` complet la nuit et a la demande | -1 run de 26 min par PR (~-35 % de minutes) | assouplit « `main` toujours entierement verifie » : compense par le run de nuit et par la PR deja verte. **Decision de l'utilisateur** |
| F | **Boucle locale rapide** : `check_gl.py --seulement <motif>`, `tools/impact.py` (liste des verifications a lancer pour les fichiers modifies, repli : toutes), garde-fou `pre-push` facultatif qui lance `--pur` (~1 min) | retour en < 1 min au lieu de 7 | la table d'impact est un conseil : le CI lance toujours tout ; un test de couverture verifie qu'aucun module de `core/` / `engine/` / `gui/` n'est sans entree |
| G | **Flux de PR** : `tools/pr_vert.ps1 <n>` (attend `complet`, `update-branch` si besoin, merge seulement si vert, jamais `--admin`, pas d'auto-merge sans demande) ; un worktree par session pour les travaux en parallele | temps d'attention, moins de collisions | le CLAUDE.md dit aujourd'hui « pas de worktree » : a amender (exception : sessions paralleles). **Decision de l'utilisateur** |
| H | **Politique des tests instables** : un test qui echoue sans changement de code est (1) diagnostique, sinon (2) inscrit dans `FRAGILES` avec relance de lui seul, jamais « relance jusqu'au vert » ; le test de latence tolerant (#119) en est le modele | moins de runs perdus | un test instable n'est jamais supprime sans fiche |
| I | **Audit de pertinence** : chaque `check_*` classe (garde / alleger / fusionner / retirer) avec son cout et ce qu'il protege ; les cinq plus chers d'abord (`foudre_render`, `stochastique`, `render`, `masters`, `doodles`) | indetermine, vient de l'audit | **l'utilisateur valide toute suppression** |

## 5. Budgets cibles

| Mesure | Aujourd'hui | Cible |
|---|---|---|
| `complet`, temps reel | 24 a 26 min | **<= 12 min** |
| `pur` | ~1 min | <= 1 min |
| Minutes facturees par PR typique | ~170 (si forfait gratuit) | **<= 60** |
| Retour local apres un changement dans un module | ~7 min (suite) ou 1 min (`--pur`) | **< 1 min** (verifications ciblees) |
| Famille labyrinthe | ~590 s | <= 250 s |
| Verifications retirees sans fiche | n/a | **0** |

## 6. Ce qui est hors perimetre

- Changer de runner (Linux, machine plus puissante) : possible plus tard ; llvmpipe sur Windows est la reference actuelle.
- Remplacer llvmpipe par un vrai GPU : le CI ne peut pas.
- Tests materiel (micro, projecteur, telephone) : restent « non verifies » et a la charge de l'utilisateur.

## 7. Decisions a prendre (resume)

1. **E** : accepter que `main` ne relance plus `complet` a chaque push (PR + nuit) ?
2. **G** : autoriser un worktree par session pour les travaux paralleles (amender le CLAUDE.md) ?
3. Confirmer la **facturation** (forfait, minutes restantes) : si le forfait est payant ou illimite, E perd de son urgence.
4. Valider les **budgets** de la section 5 (ou les durcir).

## 8. Risques

- Le gain de B depend du temps du plus gros groupe : si le groupe moteur GL reste a ~12 min apres C, la cible est tenue ; sinon il faut le couper en deux.
- Une table d'impact (F) fausse donnerait une fausse confiance locale : d'ou le repli « toutes » et le test de couverture.
- Les fiches (I) demandent du temps de relecture : elles sont le prix de la garantie de qualite.
