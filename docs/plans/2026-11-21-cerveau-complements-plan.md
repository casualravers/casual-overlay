# Cerveau : complements de la v1 et frappes qui glissent - plan

> **Pour les agents :** sous-skill requis : superpowers:subagent-driven-development (recommande) ou superpowers:executing-plans. Etapes en cases a cocher (`- [ ]`).

**But :** livrer ce que la v1 du visualizer (#192, PR #196) n'a pas livre, et corriger les frappes qui se deplacent apres avoir ete detectees.

**Architecture :** la correction est dans `core/brain.py` (pur, TDD) : le pas d'une frappe est fige quand elle est publiee. Les trois complements
suivent le chemin deja en place : le moteur publie une valeur deja calculee, `snapshot` la met en forme, `gui/gui_brain.py` la dessine.

**Pile :** Python 3.12 stdlib (core), Tk (panneau), moteur existant.

**Spec :** `docs/plans/2026-11-20-visualizer-cerveau-192.md` (sections jauges, en-tete, vue tunnel) ; ecarts listes dans la PR #196.

## Contraintes globales

- Docs, commentaires, messages CLI : francais sans accents ; libelles a l'ecran du panneau en anglais, raisons en francais.
- `core/` : stdlib seulement (`check_core_purity`) ; `core/brain.py` peut importer `core/strobe_auto.py`.
- Rien ne change a l'image projetee ; une panne du journal n'arrete jamais le rendu (`try/except` existant de `_brain_publish`).
- Les boutons ne bougent pas : tout texte ou barre nouveau a une largeur fixe ou un emplacement reserve.
- Budget du panneau : ne pas depasser les moyennes de la mesure 6 de plus de 1 ms par rafraichissement (`tools/mesure_cerveau.py`).
- Chaque nouvelle verification dans `SUITE`, et dans `PURES` si elle ne demande rien de materiel.

## Diagnostic du glissement (retour utilisateur)

`snapshot` replace a chaque rafraichissement toutes les frappes passees avec la grille **courante** (`core/brain.py:348`,
`hits_by_step(events, anchor, period)`). Or `BeatGrid.nudge` rapproche la periode de l'estimation du BPM a chaque kick
(`period += (60/bpm - period) * 0.5`). Une frappe vieille de N pas se decale de N x (variation relative de la periode) pas : 1 % de periode
= 0,67 pas a 67 pas en arriere, assez pour changer l'arrondi. Toutes les frappes anciennes glissent ensemble, d'autant plus qu'elles sont
loin : c'est exactement « la grille qui change d'echelle ». Remede : le pas d'une frappe est decide une fois, a sa publication.

## Points de vigilance (non couverts par ailleurs)

1. Saut d'ancre de phrases entieres (`nudge`) entre la publication et l'affichage : la frappe garde sa place dans la mesure.
2. Grille perdue puis retrouvee (nouvelle ancre quelconque) : les frappes d'avant sont replacees par leur date, pas par un pas faux.
3. Frappe publiee sans grille connue : placee par sa date (comportement v1).
4. Mode Foudre ou strobe auto coupe : l'en-tete n'annonce aucun strobe a venir.
5. Panneau detache puis raccroche : les barres et le voile du tunnel suivent la vue (pas d'element orphelin sur l'ancien canvas).

---

### Task 1: Pas fige a la publication

**Fichiers :**
- Modifier : `core/brain.py` (`Event`, `BrainLog.publish`, `BrainLog.set_status`, `hits_by_step`, appel dans `snapshot`)
- Test : `tools/check_gl.py` (`check_brain_pure`, nouveau cas `glissement`)

**Interfaces :**
- Produit : `Event.step: int | None`, `Event.anchor: float | None`, `Event.epoch: int` (champs avec defaut, `Event` reste `frozen`) ;
  `BrainLog` compte `_epoch` (incremente quand `set_status(grid=...)` passe d'une grille absente a une grille presente) ;
  `hits_by_step(events, anchor, period, epoch)` meme retour qu'avant.

- [ ] **Step 1 : test qui echoue** (dans `check_brain_pure`) :
  - grille (0.0, 0.5) ; kick publie a t = 10.0 (pas 80) ; `set_status(grid=(0.0, 0.495))` ; `snapshot` a t = 10.5 (tous les cas : la page affichee contient la frappe) :
    la frappe est au pas 80 de la page (aujourd'hui : 81, `round(10/0.495*4)` = 81).
  - saut d'ancre : grille (0.0, 0.5), kick a t = 10.0, puis grille (16.0, 0.5) (une phrase = 32 temps x 0.5 s) : le kick est au pas 80 - 128 = -48.
  - grille perdue (`grid=None`) puis (3.1, 0.5) : le kick est place par sa date, `step_of(10.0, 3.1, 0.5)`.
  - kick publie sans grille : place par sa date (inchange).
- [ ] **Step 2 :** `.venv\Scripts\python.exe tools/check_gl.py --seulement brain_pure` -> ECHEC sur le premier cas (81 au lieu de 80).
- [ ] **Step 3 : implementer.** Dans `publish`, sous le verrou, pour une famille de `INSTRUMENTS` ou `TRACKS` et une grille presente dans
  `_status` : `step = step_of(t, anchor, period)`, `anchor`, `epoch` stockes dans l'evenement. Dans `hits_by_step`, une frappe de la meme
  epoque est placee par
  ```
  step + PHRASE_STEPS * round((ev.anchor - anchor) * STEPS_PER_BEAT / (PHRASE_STEPS * period))
  ```
  (correction des sauts de phrases entieres seulement) ; sinon (autre epoque ou sans pas) `step_of(ev.t, anchor, period)`.
  `snapshot` passe l'epoque courante (lue avec le status). Les effets (`TRACKS`) placent leurs marques par le meme chemin.
  Le filtre `t_lo` de `snapshot` reste en dates (une marge d'un pas de plus si besoin).
- [ ] **Step 4 :** meme commande -> OK ; puis `--seulement brain` (toutes les verifications du cerveau) -> OK.
- [ ] **Step 5 : commit** `Cerveau : pas des frappes fige a la publication (Refs #192)`.

### Task 2: Jauge « sources » en barres par bande

**Fichiers :**
- Modifier : `engine/render.py` (`_brain_publish`, constante `BRAIN_BANDS_S = 0.1`), `core/brain.py` (`snapshot` recopie `bands`),
  `gui/gui_brain.py` (ligne sources vers 2177)
- Test : `check_brain_engine` (publication), `check_brain_gui` (dessin)

**Interfaces :**
- Consomme : niveaux par couche deja calcules pour l'image (ceux qui alimentent `au_bg_fx` / `logo_au` via `core.audio_layers.layer_levels`).
- Produit : `status["bands"] = {"bg": (bass, mid, high), "logo": (bass, mid, high)}`, floats 0..1, publie au plus toutes les `BRAIN_BANDS_S` ;
  `snap["bands"]` (meme forme, `{}` si absent).

- [ ] **Step 1 : tests qui echouent.** `check_brain_engine` : apres 0,25 s de rendu synthetique, `log.status()["bands"]` a les deux couches,
  3 valeurs dans [0, 1] ; une couche en source « rien » donne (0, 0, 0). `check_brain_gui` : panneau construit avec un instantane
  `bands={"bg": (1, 0.5, 0), "logo": (0, 0, 0)}` : 6 barres (tag `band`), la barre bass du fond a la largeur pleine, celle du logo 0 ;
  la largeur de la ligne ne change pas entre `(0,0,0)` et `(1,1,1)`.
- [ ] **Step 2 :** `--seulement brain_engine --seulement brain_gui` -> ECHEC (cle `bands` absente).
- [ ] **Step 3 : implementer.** Moteur : publier `bands` avec son propre minuteur (pas avec la sante, qui reste a 1 s). Panneau : par couche,
  libelle `bg` / `logo`, nom de la source (comme en v1) puis 3 barres `B M H` de largeur fixe, mises a jour par `coords` (pas de recreation).
- [ ] **Step 4 :** meme commande -> OK.
- [ ] **Step 5 : commit** `Cerveau : barres par bande de la jauge sources (Refs #192)`.

### Task 3: « build en cours, strobe a venir » dans l'en-tete

**Fichiers :**
- Modifier : `core/brain.py` (`snapshot`, en-tete), `gui/gui_brain.py` (en-tete vers 2161)
- Test : `check_brain_pure`, `check_brain_gui`

**Interfaces :**
- Consomme : `core.strobe_auto.BUILD_CONFIRM_S`, `core.strobe_auto.auto_build_hz(elapsed_s) -> float | None` ; `gauges["auto"]["strobe"]`.
- Produit : `header["strobe_in_s"]: float | None`, `header["strobe_hz"]: float | None`.

- [ ] **Step 1 : tests qui echouent.** `check_brain_pure` : montee depuis 1,5 s, strobe auto actif : `strobe_in_s == 2.5`, `strobe_hz is None` ;
  depuis 6 s : `strobe_in_s == 0` et `strobe_hz == auto_build_hz(6.0)` ; strobe auto coupe ou pas de montee : les deux `None`.
  `check_brain_gui` : texte d'en-tete `build 1.5 s  strobe in 2.5 s`, puis `build 6 s  strobe 4 Hz` (format des valeurs a l'implementation,
  libelles exacts `build`, `strobe in`, `strobe`), et la largeur de l'en-tete identique dans les trois etats.
- [ ] **Step 2 :** `--seulement brain_pure --seulement brain_gui` -> ECHEC.
- [ ] **Step 3 : implementer** le calcul dans `snapshot` (le strobe auto ne s'annonce que s'il est actif dans les jauges ; en mode Foudre,
  `strobe_kind` publie dans les jauges l'eteint aussi) et le texte dans un emplacement de largeur reservee.
- [ ] **Step 4 :** meme commande -> OK.
- [ ] **Step 5 : commit** `Cerveau : strobe a venir pendant une montee (Refs #192)`.

### Task 4: Eclair du strobe qui blanchit tout le tunnel

**Fichiers :**
- Modifier : `gui/gui_brain.py` (`TunnelView`, eclair vers 1435-1458)
- Test : `check_brain_tunnel`

**Interfaces :**
- Consomme : evenements `strobe` / `foudre` deja dans l'instantane (meme declencheur qu'en v1).
- Produit : rien pour les autres taches.

- [ ] **Step 1 : test qui echoue.** Instantane avec un `strobe` il y a 0,05 s : un rectangle tag `flash` existe, plein cadre, `fill` blanc,
  `stipple` non vide, empile au-dessus de toutes les cellules et sous tous les libelles (`find_above` / ordre de `find_all`) ; 0,4 s plus tard
  il est cache (`state hidden`) ; detacher puis raccrocher le panneau ne laisse aucun `flash` visible sur l'ancien canvas.
- [ ] **Step 2 :** `--seulement brain_tunnel` -> ECHEC.
- [ ] **Step 3 : implementer.** Un seul rectangle cree avec la vue (pas a chaque eclair), stipple `gray50` puis `gray25` pour l'extinction
  (Tk n'a pas d'alpha), remonte au-dessus des cellules avec `tag_raise(flash, cellules)` puis `tag_raise(libelles)`. L'eclaircissement du fond de
  la v1 reste.
- [ ] **Step 4 :** meme commande -> OK.
- [ ] **Step 5 : commit** `Cerveau : eclair du strobe sur tout le tunnel (Refs #192)`.

### Task 5: Fiche, mesure, PR

**Fichiers :**
- Modifier : `docs/fonctions/cerveau.md` (pas fige et sa raison, barres, strobe a venir, voile du tunnel ; la section « Non livre » disparait),
  description de la PR #196 (section « Non livre en v1 » retiree, verifications ajoutees)

- [ ] **Step 1 :** `.venv\Scripts\python.exe tools/check_gl.py --pur` -> OK.
- [ ] **Step 2 :** `.venv\Scripts\python.exe tools/mesure_cerveau.py` (projection ouverte, 60 s par vue) : moyennes par vue au plus
  +1 ms par rapport a la mesure 6 (iso 5,2 / mandala 5,3 / tunnel 9,9 / sismo 8,5 / libre 3,5) ; resultats notes dans la fiche.
- [ ] **Step 3 :** suite complete en arriere-plan, seule (~7 min) : seuls les 3 echecs connus de main.
- [ ] **Step 4 : commit** `Cerveau : fiche et mesure des complements (Refs #192)` ; push de la branche, mise a jour de la PR.

Non verifie (materiel) : glissement avec la vraie musique, lisibilite des barres dans le noir, eclair du tunnel sur l'ecran du PC de soiree.
