# Densite variable du labyrinthe : lignes serrees pres d'un « aimant » (#160)

Spec a valider dans le chat. Une seule PR : cette spec, son plan et le code. Fiche a lire : [docs/fonctions/fond.md](../fonctions/fond.md) (labyrinthe, zones, Vide, masque #178).

## Ce que veut l'utilisateur

Faire cohabiter **plusieurs niveaux de densite de pattern dans une meme image** : comme dans les simulations de ferrofluide, ou un aimant concentre les lignes autour de lui (lignes serrees pres du foyer, de plus en plus espacees en s'eloignant).

Reponses du 2026-11-27 :
- **Densite** : **les deux au choix** : « aimant » (un ou plusieurs foyers) ou « zones » (plages serrees / larges qui derivent, sans centre) ;
- **Position** : **derive automatique lente**, comme le masque (periodique, boucle), pas de curseur X / Y ;
- **Reglages** : intensite, portee, nombre de foyers, derive.

## Mesure qui fonde la methode (poste de dev, Radeon integree)

L'espacement des lignes de Gray-Scott depend de la **diffusion** : on multiplie le laplacien (U et V) par un facteur `ds` et on mesure la longueur d'onde du motif apres 6000 pas (labyrinthe, grille 384 x 216) :

| `ds` | 1,0 | 0,7 | 0,5 | 0,35 | 0,25 |
|---|---|---|---|---|---|
| longueur d'onde (cellules) | 16,1 | 13,4 | 11,3 | 9,3 | 7,7 |

soit une longueur d'onde en **racine de `ds`**, jusqu'a **~2 fois plus fin** a `ds` = 0,25, avec la meme couverture (0,29 a 0,30). On **ne peut pas aller plus large** que le reglage actuel : la diffusion actuelle (`Du` = 1, `dt` = 1, laplacien 3 x 3 de Karl Sims) est a la limite de stabilite. La densite est donc **« plus serre que la taille du motif reglee »** (curseur « Taille du motif » inchange) : ds = 1 hors foyer, `DENS_MIN_DS` = 0,25 au foyer a intensite 1.

## Principe

La carte (F, K) calculee une fois par image (`gl_shaders/rd_zone.frag`, lue a chaque pas par `rd_step.frag`) gagne un **troisieme canal : `ds`**, le facteur de diffusion de la cellule. `rd_step.frag` fait `lap *= ds` avant le pas. Aucun pas de simulation de plus, aucune passe de plus : seule la carte, une fois par image, calcule `ds = 1 - (1 - DENS_MIN_DS) x intensite x d(uv)` avec `d` (0..1, 1 = le plus serre) selon le mode. **Neutre** : sans densite `ds` = 1 et `lap x 1,0` est exact : simulation identique bit pour bit a la version d'avant.

## La densite `d(uv, t)` (formules pures dans `core/reaction.py`, repliquees en GLSL)

- **Aimant** : `nombre` foyers (1..4), positions deterministes qui **derivent** lentement sur le tore (la grille boucle : un foyer qui sort a droite rentre a gauche), `d = max sur les foyers de (1 - smoothstep(0, rayon, distance))`, distance sur le tore en unites isotropes (rond a l'ecran), rayon = `portee` x une demi-hauteur d'image (0,1 .. 1 -> 0,05 .. 0,5 hauteur... `DENS_REACH_MAX` a regler au rendu).
- **Zones** : le bruit du masque (`pnoise` salé, deux octaves) a une finesse reglee par la portee (portee grande = peu de grandes zones), `d = smoothstep(0,35 ; 0,8 ; bruit)` : plages serrees et plages larges qui derivent.
- Coordonnees **repliees par la symetrie** de colonisation (`fold`, comme les formes, le Vide et le masque) : avec une symetrie, les foyers se repetent.
- `none` : `ds` = 1 partout, chemin du shader non parcouru (`u_dens` = 0).

## Reglages (table `core/params_schema.py`, `scope="bg"`)

| Cle | Defaut | Plage | Role |
|---|---|---|---|
| `bg_rd_dens` | `none` | `none` / `magnet` / `zones` | mode de densite ; `none` = comme avant |
| `bg_rd_dens_amount` | 0,7 | 0 .. 1 | intensite (0 = rien, 1 = ~2 fois plus serre au foyer) |
| `bg_rd_dens_reach` | 0,4 | 0,1 .. 1 | portee du foyer / taille des zones |
| `bg_rd_dens_count` | 1 | 1 .. 4 | nombre de foyers (aimant) |
| `bg_rd_dens_drift` | 0,5 | 0 .. 2 | vitesse de derive (x `DENS_SPEED` 0,03 cycle / s) |

**Automatisables** (coupes par defaut) : intensite et portee. Enregistres avec **scenes, presets de fond** (`BG_KEYS`) et le **Full Randomizer** (labyrinthe : mode tire une fois sur trois, reglages dans leurs plages, tire apres tout le reste).

## GUI (`gui/gui_fond.py`, bloc labyrinthe `rd_box`)

Radio « Densite » (Aucune / Aimant / Zones), curseurs « Intensite de la densite », « Portee », « Nombre de foyers », « Derive de la densite » ; grises sans mode, « Nombre de foyers » grise hors Aimant (`gui_gates`, lignes cherchees dans `rd_box`). Pas de vignette : l'effet se lit directement sur le motif.

## Cas limites et risques

- **Adaptation lente** : le motif ne se reorganise qu'au rythme de la simulation (centaines de pas) : un foyer qui bouge vite laisse une trainee de lignes encore serrees. Derive lente a dessein (`DENS_SPEED`), a regler au rendu. A verifier : pas d'artefacts durables a l'interface de deux densites (defauts, ruptures) ; sinon `DENS_MIN_DS` plus haut.
- **Cumul avec le masque, le Vide, la variete** : independants (le masque / Vide eteignent, la densite change l'espacement, la variete la forme). Un foyer sur une zone vide n'a pas d'effet visible.
- **Stabilite** : `ds` <= 1 partout, donc jamais plus instable qu'avant.
- **Cout GPU** : la carte passe de RG32F a **RGBA32F** (plus de bande passante a chaque pas : un float en plus), a mesurer a 1080 p sur le labyrinthe charge (grille 1280 x 720, 10 pas) ; `core/safety.py` : meme cout de zone si la densite est active (une passe de carte).
- **Taille de grille changee** (`bg_rd_scale`) : la carte est realloquee et recalculee a l'image suivante.
- **Reinitialisation** : changer un reglage ne reensemence pas.
- **Fond non labyrinthe** : reglages sans effet (grises).

## Fichiers

`core/reaction.py` (`Dens`, `Zone.dens`, `dens_points`, `dens_value`, `diffusion_scale`, `dens_uniforms`), `core/params_schema.py`, `core/automation.py`, `core/randomizer.py`, `core/safety.py`, `gl_shaders/rd_zone.frag` (canal `ds`), `gl_shaders/rd_step.frag` (`lap *= ds`), `engine/reaction.py` (carte RGBA32F, uniforms), `engine/render.py` (temps de derive integre), `gui/gui_fond.py` + `gui/gl_gui.py`, `docs/fonctions/fond.md`, `tools/check_gl.py` (les lectures de `zone_tex` du masque et des zones passent par un petit utilitaire : 4 canaux).

## Verifications

- **Pur** : `dens_value` (aimant : 1 au foyer, 0 hors portee, decroit avec la distance, tore, derive continue et periodique, nombre de foyers ; zones : part de l'image dense raisonnable sur la derive, continu), `diffusion_scale` (1 sans mode / intensite 0, `DENS_MIN_DS` au foyer a intensite 1, borne), symetrie, reglages et automatisables.
- **Rendu** : `none` = simulation identique bit pour bit (aussi avec zones / masque actifs) ; la carte GPU concorde avec la fonction pure ; **les lignes sont plus serrees au foyer que loin** (longueur d'onde mesuree par transformee de Fourier de deux regions, au moins 25 % plus fin) apres une simulation ; deux foyers ; derive qui deplace la zone dense ; cohabite avec le masque.
- **GUI** : lignes presentes, grisages ; Randomizer ; scene / preset de fond.
- **Non verifie (materiel)** : lecture au videoprojecteur, derive en soiree, cout sur la machine du VJ.

## Hors de cette PR

Densite plus **large** que le reglage de base (demande de refondre la diffusion de base) ; foyer place sur le logo ou reagissant au son ; densite dessinee (image) ; deformation reelle des lignes (champ magnetique, lignes qui s'alignent sur le foyer).
