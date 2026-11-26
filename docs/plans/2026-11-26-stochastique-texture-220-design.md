# Stochastique : texture a l'interieur de la tache (#220)

Spec validee dans le chat le 2026-11-26. Une seule PR : cette spec, son plan et le code. Voir `docs/fonctions/fond.md`.

## Ce que veut l'utilisateur

Sur la palette **stochastique**, les points qui se touchent forment une **tache** (leur union). La **zone delimitee par cette tache** est aujourd'hui un aplat de couleur 1 : on veut la **texturer**. Trois choix : aucune (image d'origine), **onde sonore**, **pavage reaction-diffusion balaye**.

Reponses de l'utilisateur (questions du 2026-11-26) :
- zone = l'union de tous les points (la tache), pas chaque point a part ni le fond entre les points ;
- onde sonore = **lignes d'oscilloscope** (pas de vrai spectre : le shader n'a que basses / mediums / aigus / kick) ;
- pavage = **une vague peint puis efface** : un front traverse la tache (direction = `bg_angle`) et fait pousser le motif derriere lui ; a l'intervalle suivant, un nouveau front efface l'ancien motif et en redessine un autre ;
- intervalle = **secondes ou mesures au choix** (interrupteur « Caler sur la mesure ») ;
- couleur de la texture = **la couleur 1 eclaircie** (aucun nouveau selecteur de couleur).

## Reglages (table `core/params_schema.py`, tous du fond)

| Cle | Defaut | Plage | Role |
|---|---|---|---|
| `bg_sto_tex` | `none` | `none` / `wave` / `tiling` | texture interieure ; `none` = image d'origine, pixel pour pixel |
| `bg_sto_tex_scale` | 1,0 | 0,3 .. 3,0 | finesse : espacement des lignes (onde) ou taille du motif (pavage) |
| `bg_sto_tex_contrast` | 0,6 | 0 .. 1 | eclaircissement de la couleur 1 sur les lignes / le motif (0 = aplat, 1 = presque blanc) |
| `bg_sto_tex_every` | 8,0 | 2 .. 30 s | intervalle entre deux vagues du pavage |
| `bg_sto_tex_sync` | 0 | 0 / 1 | 1 = l'intervalle se compte en mesures (BPM) et la vague repart sur le temps 1 |
| `bg_sto_tex_bars` | `4` | `1` / `2` / `4` / `8` | mesures entre deux vagues quand `bg_sto_tex_sync` = 1 |

GUI : un groupe de boutons « Texture » (Aucune / Onde sonore / Pavage) dans `sto_box`, puis les curseurs ; l'intervalle (curseur, case, mesures) n'est actif que pour le pavage (`gui_gates`). Full Randomizer : texture tiree (aucune une fois sur deux), reglages dans leur plage.

## Rendu (`gl_shaders/scene.frag`, `stochastic_pattern`)

Le calcul de la tache ne change pas (`cover`, `fdot`, bordure). Apres lui, la couleur des points `dots` est remplacee par `mix(dots, lighten(dots, contrast), tex(p))` ou `tex` est un masque 0..1 **continu a travers la tache** (calcule en coordonnees ecran, pas par point : les lignes / le motif ne sont pas coupes entre deux points voisins). La bordure (#219) reste par-dessus. Cout : un calcul de masque par pixel couvert (onde : un `sin` ; pavage : une lecture de tuile).

- **Onde sonore** (`tex = wave`) : lignes horizontales d'espacement `~ 12 px x scale` (pour 720 px de haut) ondulant d'une amplitude `a` et d'une phase qui avance. Audio (`u_sto_au` existant + un uniform `u_sto_wave` : basses, mediums, aigus, kick, phase) : **basses** = amplitude, **aigus** = frequence de l'ondulation, **kick** = coup d'amplitude et de largeur des lignes, **mediums** = vitesse de defilement. Silence = lignes quasi droites, immobiles.
- **Pavage** (`tex = tiling`) : on **reutilise la tuile de Gray-Scott du remplissage du texte** (`engine/textfill.py`, `TextPattern`, forme corail, calculee une fois, mise en cache disque `~/.audio2wave/text_fill_tile.npz`, ~4 s la premiere fois, deja geree par `Renderer` quand le texte l'utilise : une tuile partagee, jamais deux). La tuile donne `(V, instant d'arrivee)` ; la vague : `tau = temps_depuis_depart - distance_au_bord_de_depart x front` avec la distance mesuree **le long de `bg_angle`** ; `core.textfx.fill_reveal` donne l'age (front net, motif qui s'epaissit derriere). A chaque intervalle : `tau` repart de zero, la tuile est relue avec un **decalage aleatoire** (nouveau pavage) et l'ancien motif est efface par le nouveau front (le front efface ce qu'il traverse : derriere lui le nouveau motif, devant lui l'ancien). Tant que la tuile n'est pas prete : pas de texture (aplat), sans erreur.

## Cadence (Python pur, `core/`)

Nouveau module pur `core/stotex.py` (TDD) : `wave_params(levels, ...)` (amplitude, frequence, vitesse a partir des bandes, bornees et lissees comme `core.explain`), `sweep_state(now, last_start, every_s, bars, sync, bar_phase) -> (age_s, epoch, restart)` : quand repartir, numero de l'epoque (graine du decalage de tuile), instant du depart. En mode mesure on part sur le temps 1 de la mesure (`core.controls.BarTimer`, deja la) ; sans BPM fiable (silence, telephone) on retombe sur l'intervalle en secondes. Aucun import hors stdlib.

## Cas limites

- `none` : image strictement identique a avant, aussi bien avec bordure, paliers ou douceur.
- Palette stochastique inversee (`bg_sto_invert`), arc-en-ciel, teinte (`bg_hue`) : la texture est calculee sur `dots` apres inversion / arc-en-ciel, avant la rotation de teinte globale.
- Sans son : l'onde est calme et fixe ; le pavage n'a pas besoin de son (cadence interne).
- Preset de fond / scene : reglages du fond (`BG_KEYS`), donc sauvegardes avec le fond ; une scene sans ces cles les prend a `none`.
- Recalcul de la tuile en cours (premier lancement, ~4 s) : le mode pavage montre l'aplat puis le motif des qu'elle est prete ; `PerfGuard.active` ralentit son calcul (deja le cas).
- GPU : le pavage ne coute que des lectures de tuile (+0,1 a +0,3 ms mesure attendue, a mesurer) ; pas d'ajout a `EFFECT_COST_MS` sauf mesure contraire.

## Verifications

`core/stotex.py` : tests purs (bornes, lissage, intervalle en secondes et en mesures, repli sans BPM, epoques distinctes). Rendu : `none` = image d'origine ; onde : meme masque de zone (la zone couverte ne change pas), lignes visibles (variance interne > seuil), basses = amplitude, silence = lignes droites ; pavage : tuile absente = aplat, tuile prete = motif a l'interieur seulement, front qui avance (couverture croissante dans le temps), nouveau depart = nouveau motif ; GUI : groupe « Texture », grisage de l'intervalle ; randomizer : plages. Non verifie (materiel) : lecture au videoprojecteur, calage sur la mesure avec la vraie musique.
