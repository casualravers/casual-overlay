# Routage audio par effet (#198)

Spec a valider. Issue : « Pouvoir assigner independamment a chaque effet une bande de frequence (ou un element) ».
Statut : brainstorming termine dans le chat (perimetre, usage, interface valides). Aucun code ecrit.

## Besoin (valide avec l'utilisateur)

- **Perimetre** : tout ce qui reagit au son, pas seulement les 4 effets du shader.
- **Usage** : on compose une scene (reglage une fois, sauve avec la scene). Pas de geste « live » dedie.
- **Zero perte** : rien de l'existant n'est retire ni remplace. Une scene ou un preset existant rend la meme image, pixel pour pixel. Le routage est une couche de controle plus fine PAR-DESSUS les reglages de couche.
- **Deux endroits, une seule donnee** : (1) un onglet **Routage** qui montre TOUS les routages ; (2) une **pastille de source juste a cote de chaque effet** (dans son onglet) qui edite la meme valeur. Modifier l'un met l'autre a jour.
- **Interface = cle du ticket** : tableau lisible + mini-spectre (maquettes validees dans le chat, rappelees plus bas).

## Ce qui existe deja et RESTE tel quel

Sources par couche (`src_bg`, `src_logo`), sensibilite / retard / lissage par couche (`sens_*`, `lag_*`, `smooth_*`), lien fond / logo (`fx_link`), source `auto` et son tableau phase x couche (`AUTO_TABLE`), reactivite par effet en auto (`FX_TABLE`, `EffectBank`), `ripple_beat`, renforts de fin de cycle (`cycle_accent`), masters de reactivite et phases (appliques AVANT le routage : la panique agit donc sur toutes les lignes). Menus « Le fond / Le logo reagit a » : conserves, ils deviennent le reglage par defaut des lignes « comme la couche ».

## Sources proposees dans les menus

Toutes les valeurs de `audio_layers.SOURCES`, regroupees en familles (le menu garde ses separateurs) :
- **Reglage** : `herite` (nouveau, defaut : « comme la couche »), `auto`, `rien`.
- **Niveaux** : tout, basses, mediums, aigus.
- **Bandes** : sub, corps, presence, air.
- **Rythme** : kick, frappes, caisse claire, elan.
- **Structure** (rien n'est perdu) : pouls, croches, doubles, mesure, phrase8, phrase16, phrase32, fin8, fin16, fin32.

## Inventaire des lignes (ce qui reagit au son aujourd'hui)

Releve dans `Renderer.draw` (engine/render.py) ; l'identifiant est la cle de reglage (`rt_<id>`, `rg_<id>`).

| Zone | id | Ce qu'il pilote aujourd'hui (vecteur lu) |
|---|---|---|
| Fond | `bg_wobble` `bg_ripple` `bg_glitch` `bg_chroma` | les 4 effets du shader, couche fond (`au_bg_fx[0..3]`) |
| Fond | `bg_flash` | flash du motif au kick (`u_beat`, beat du fond) |
| Fond | `bg_scroll` | defilement du motif et bascule du damier (basses, kicks) |
| Fond | `bg_sparkle` | scintillement du motif stochastique / labyrinthe (aigus) |
| Logo | `lg_wobble` `lg_ripple` `lg_glitch` `lg_chroma` | les 4 effets du shader, couche logo (`au_logo_fx[0..3]`) |
| Logo | `lg_pulse` | pulsation de taille (`logo_pulse` x beat) |
| Logo | `lg_jitter` | tremblement (`logo_jitter` x aigus) |
| Logo | `lg_glow` | rayon / eclat du contour lumineux (basses) |
| Logo | `lg_holo` | halo holographique (`holo_react`, basses) |
| Logo | `lg_melt` | fonte acide (`melt_react`, basses) |
| Logo | `lg_cells` | cellules (`cell_react`, basses et kick) |
| Global | `breath` | respiration sur la pulsation (`breath_pulse`) |
| Global | `hue` | teinte spectrale (`hue_spectrum`, basses / aigus) |
| Global | `strobe` | strobe auto (`StrobeBrain`) ; ligne informative d'abord (il decide deja seul) |
| Global | `foudre` | foudre (`core/foudre.py`) |
| Global | `noise` | noise (ses cases propres restent) |

**Hors inventaire (decision)** : mosaique de doodles et texte (vague, remplissage) ne reagissent PAS au son aujourd'hui (seulement a l'horloge) : pas de ligne. En faire des cibles audio serait une nouvelle fonction, a part.

## Modele de donnees

Deux reglages par ligne, dans `core/params_schema.py` (donc validation, presets overlay, scenes, scenes anciennes compatibles) :
- `rt_<id>` : choix dans `("herite", *SOURCES)`, defaut `herite`, portee `look`.
- `rg_<id>` : dosage 0..2, defaut 1.0, portee `look`.

Une scene ancienne n'a pas ces cles : defauts appliques, rendu identique. Le tableau des lignes (id, zone, libelle, couche) vit dans `core/routing.py` (pur) : `ROUTES`, `route_of(params, id)` -> `(source, dose)` ; `is_default(params)` (aucune ligne modifiee).

## Moteur

- **Regle de resolution** par ligne : `herite` + dose 1.0 -> le vecteur existant, inchange (chemin actuel, zero cout, zero difference). Sinon la ligne calcule SON vecteur : `SourceMixer` + `LayerFilter` propres a la ligne (memes `lag_*` / `smooth_*` que la couche), source resolue (`auto` -> colonne du tableau pour la couche), multiplie par le dosage. Seules les lignes modifiees coutent quelque chose.
- Une ligne reglee a la main sort d'Auto pour CET effet seulement (les autres lignes de la couche restent en auto) : meme principe que la source a la main d'aujourd'hui.
- Un vecteur par ligne remplace, a l'endroit ou `draw` les lit, le vecteur de couche (`bg_au`, `lg_au`, `au_*_fx`). Les masters / phases ont deja ete appliques a `state`.
- Cerveau (#192) et voyants : `Renderer.routes_now` -> `s.status["routes_now"]` (source resolue et niveau par ligne), lu par l'onglet Routage pour le temoin de niveau.

## Interface

**Onglet Routage** (nouveau, `gui/gui_routing.py`) :
- En-tete : **mini-spectre** 20 Hz - 20 kHz ; survoler ou selectionner une ligne y surligne sa plage (bandes -> leur zone ; kick, frappes, structure -> pas de plage, une pastille d'evenement a la place) et montre l'activite en direct.
- Corps : tableau regroupe Fond / Logo / Global. Par ligne : libelle, menu de source (familles ci-dessus, avec le texte d'Auto : « Auto : groove -> kick »), temoin de niveau (barre), dosage (curseur 0..200 %), bouton de remise a « comme la couche ».
- Une ligne modifiee est marquee (pastille bleue) ; un bouton « Tout remettre » par zone.
- Largeur constante, boutons qui ne bougent pas (regle de l'interface), lignes grisees quand l'effet est coupe.

**Pastille a cote de l'effet** (`routing_pill(parent, id)` dans `gui/gui_routing.py`, posee par `gui_effects` / `gui_logo` / `gui_fond` / `gui_audio` a cote du curseur d'intensite) : largeur fixe ; grise « Comme le fond » (defaut), bleue « Basses 80 % » quand reglee ; clic = menu des familles + dosage. Meme variable Tk que le tableau (une source de verite : le reglage), donc rien a synchroniser a la main.

## Decoupage en PR (une spec, trois livraisons)

1. **Modele + moteur, 8 effets shader (fond et logo) + `core/routing.py` + reglages + tests purs et pixels.** Pas d'interface (reglable via fichier / scene). Verifie : image identique avec routes par defaut.
2. **Onglet Routage + pastilles sur les 8 effets.**
3. **Les autres lignes** (motif, logo CPU, halo, fonte, cellules, globaux) + pastilles.

## Verifications (check_gl.py ; nouveaux noms dans `SUITE`, purs aussi dans `PURES`)

- `check_routage` (pur) : table des lignes, schema (defauts, plages, anciennes scenes), resolution (herite, a la main, auto, dosage), `is_default`.
- Pixels : routes par defaut = image identique a avant (reference) ; ondulation du fond sur `basses` + aberration du logo sur `kick` : seul l'effet concerne bouge ; dosage 0 = effet immobile ; ligne en auto a cote d'une ligne a la main.
- GUI : pastille et tableau synchronises ; largeur constante de l'onglet ; une scene sauvee / rechargee garde ses routes.
- **Non verifie** (materiel) : lisibilite de l'onglet au projecteur, ressenti des affectations sur de la vraie musique.

## Risques

- **Perf** : seulement les lignes modifiees coutent ; budget a mesurer avec 20 lignes modifiees (`PerfGuard`, `core/safety.py` : pas de cout GPU, cout CPU Python par ligne).
- **Largeur de la fenetre** : 16 pastilles dans les onglets existants ; verifier `check_gui` (largeur constante).
- **Conflits** : touche `engine/render.py`, `core/params_schema.py`, `tools/check_gl.py`, `gui/*` : une PR a la fois, pas de PR parallele sur ces fichiers.
- **Strobe / foudre** : decident deja seuls (phases, grille) ; en PR 3 seulement, d'abord en lecture (voyant), a confirmer avant de les rendre routables.

## Decisions prises a l'execution (PR 1)

- **22 lignes, pas 23** : le compte du plan etait faux (7 fond, 10 logo, 5 globales) ; la table de l'inventaire ci-dessus est la bonne.
- **Logo lie** (`fx_link` = 1, defaut) : les 4 effets shader du logo suivent la ligne du FOND (`routing.fx_route_id`), comme ils suivent deja la source, le retard et le lissage du fond ; leur propre ligne est ignoree (elle sera masquee dans l'interface, comme les reglages du logo lie). Sans cela, router « Ondulation du fond » laissait le logo onduler sur les basses.
- **Reglages par la table** : `routing.route_specs()` rend des tuples que `params_schema` transforme en `_p` (pas d'import de `params_schema` dans `routing` : import circulaire).
- **Cout mesure** : +0,05 a +0,14 ms CPU par image avec 8 lignes routees (poste de dev, 1280x720, mediane) ; 0 avec les routes par defaut.

## Documentation

Section « Routage audio par effet » dans `docs/fonctions/audio.md` (modele, resolution, zero perte) et dans `docs/fonctions/gui.md` (onglet, pastille) ; ligne dans l'index de `CLAUDE.md` seulement si une nouvelle fiche est creee (non prevu).
