# Overlay Texte : vague de lettres (ola) et remplissage par reaction-diffusion (spec, Closes #199)

Spec ecrite apres brainstorming avec l'utilisateur (2026-11-25). A valider avant le code (une seule PR : spec + code).

## But

Donner au texte de l'overlay une vie propre au projecteur, sans toucher au logo (image / video) ni aux autres overlays. Deux effets,
chacun avec sa case a cocher, **cumulables** entre eux et avec le halo, la fonte acide, les cellules et la mosaique :

1. **Vague de lettres (« ola »)** : les lettres s'eteignent en fondu de gauche a droite, puis se rallument dans le meme sens.
2. **Remplissage par reaction-diffusion** : l'interieur des lettres est colonise vite par un motif organique.

Rien n'est coche par defaut : le rendu est celui d'avant.

## Decisions (reponses de l'utilisateur)

| Sujet | Decision |
|---|---|
| Vague : granularite | **Lettre par lettre** (chaque lettre a son instant), pas un front vertical qui coupe les lettres |
| Vague : rythme | **En boucle**, balayage complet (extinction puis rallumage) **~6 s** (3 s a l'origine, ralenti de moitie apres essai), recommence **toutes les ~20 s** ; horloge libre en secondes |
| Remplissage : couleurs | **Couleur du texte** ; le motif joue sur la **luminosite** (zones claires et sombres dans les lettres) |
| Remplissage : renouveau | Repart de zero **au rallumage de la vague** quand elle est cochee, **a chaque changement de texte**, et **toutes les N s** quand la vague n'est pas cochee |
| Cases « macro » | Dans l'**espace de controle global du Mode VJ** (onglet *Masters*, a cote d'Overlay), en plus des reglages detailles de la partie OVERLAY |
| Presets et scenes | Les deux effets et leurs reglages sont **sauves dans les presets overlay et les scenes** (portee `look`) |

## Comportement

### Vague de lettres

- **Rang d'une lettre** : de 0 (gauche) a 1 (droite), selon la position de son centre sur la largeur de **tout** le texte. Sur plusieurs
  lignes, toutes les lignes balaient ensemble (a un meme x, meme instant). Les espaces n'ont pas de pixels, donc pas de rang.
- **Cycle** de periode `P` = `text_ola_every` s ; `tau` = temps depuis le debut du cycle ; `D` = `text_ola_dur` ; `F` = `text_ola_fade`
  (borne a `D / 2`). Opacite de la lettre de rang `r` :
  - `tau < D/2` (extinction) : `1 - smoothstep(r (D/2 - F), r (D/2 - F) + F, tau)`
  - `D/2 <= tau < D` (rallumage) : `smoothstep(D/2 + r (D/2 - F), D/2 + r (D/2 - F) + F, tau)`
  - `tau >= D` : 1 (texte entier, lisible pendant `P - D` s, soit 17 s sur 20 par defaut).
  La derniere lettre finit de s'eteindre pile quand la premiere commence a revenir (une vague continue).
- Le premier cycle **demarre a l'activation** de la case (on voit tout de suite l'effet), puis toutes les `P` s.
- La vague **multiplie l'opacite des lettres** (avant le contour lumineux et les effets du logo) : les lettres eteintes disparaissent
  vraiment, et le halo, la fonte, les cellules et la mosaique s'appliquent sur ce qui reste. Elle se **multiplie** avec le fondu des
  textes en rotation (`text_fade`, 0,2 s) : les deux horloges restent independantes.

### Remplissage par reaction-diffusion

- Simulation Gray-Scott sur une **grille reduite** (cote long <= 384 cellules) couvrant la meme etendue que la texture du texte, **limitee
  a l'interieur des lettres** : le masque (alpha du texte) sert de mur, rien ne diffuse dehors. Elle ne tourne que si la case est
  cochee **et** que la source est Texte.
- Le motif part de quelques germes places dans les lettres, avec des parametres de colonisation rapide (remplit un texte en ~1 a 2 s
  a `text_fill_speed` = 1).
- Rendu dans `logo_at()` : `couleur *= 1 - contraste x (1 - motif)` (motif = 1 clair, 0 sombre) ; la teinte ne change pas, l'opacite non plus.
- **Remise a zero** (germes neufs) : (a) au rallumage de la vague (`tau = D/2`) quand `text_ola_on` ; (b) a chaque changement de texte
  (texte principal ou rotation) ; (c) toutes les `text_fill_every` s quand la vague n'est pas cochee.

### Panique, securite GPU, enregistrement

- **Panique (X)** : coupe les deux effets (`PANIC_RESET` de `core/safety.py`), le texte redevient plein et lisible.
- **Securite GPU du VJ** : le remplissage est declare comme effet couteux (`EFFECT_COST_MS`, cout a mesurer) : une scene qui l'allume peut
  etre allegee. La simulation reduit ses pas par image quand `PerfGuard` signale un GPU trop charge.
- **Enregistrement** (video verticale comprise) : les deux effets sont dans l'image rendue par le moteur, donc dans les videos ; teste.

## Reglages (`core/params_schema.py`, portee `look`)

| Cle | Defaut | Plage | Role |
|---|---|---|---|
| `text_ola_on` | 0 | 0..1 | Vague : interrupteur |
| `text_ola_dur` | 6,0 | 1..10 s | Duree d'un balayage complet (extinction + rallumage) |
| `text_ola_every` | 20 | 5..120 s | Periode de la boucle (>= `text_ola_dur`) |
| `text_ola_fade` | 1,0 | 0,1..1,5 s | Duree du fondu d'une lettre |
| `text_fill_on` | 0 | 0..1 | Remplissage : interrupteur |
| `text_fill_speed` | 1,0 | 0,3..3 | Vitesse de colonisation (pas de simulation par image) |
| `text_fill_scale` | 1,0 | 0,5..2,5 | Finesse du motif |
| `text_fill_contrast` | 0,6 | 0..1 | Ecart clair / sombre |
| `text_fill_every` | 20 | 5..120 s | Periode de remise a zero sans vague |

## Architecture

- **`core/textfx.py`** (nouveau, Python pur, TDD dans `check_gl.py`, dans `PURES`) : `ola_alpha(rank, tau, dur, fade)` ; `OlaClock`
  (temps injecte : `tau`, indice de cycle, bord montant « rallumage » pour le remplissage) ; `FillClock` (decide les remises a zero :
  rallumage, changement de texte, periode). Zero import du moteur / de la GUI.
- **`engine/render.py`** : `text_rank_map(rgba)` -> carte du rang (uint8, meme taille que la texture du texte) calculee **depuis l'alpha** :
  bandes de lignes (projection horizontale), puis groupes de colonnes contigues par ligne = « lettres », rang = centre normalise sur toute
  la largeur. Les accents et points (i, j) sont dans les colonnes de leur lettre. *Limite connue* : des lettres qui se touchent (police
  script) forment un seul groupe. La carte est regeneree avec `logo_signature` (comme la texture du texte), jamais a chaque image.
- **`engine/textfill.py`** (nouveau) : classe `TextFill` (grille ping-pong RG, masque, `step(n)`, `reset(seed)`), shader
  **`gl_shaders/textfill_step.frag`** (Gray-Scott avec masque). Cree a la demande, detruit quand l'effet est coupe.
- **`gl_shaders/scene.frag`** (`logo_at`) : uniformes `u_text_ola` (phase, duree, fondu), carte du rang et texture du remplissage ; applique
  le remplissage (couleur) puis la vague (opacite), avant le contour et les effets du logo. Aucun effet si l'un des deux est coupe
  (image strictement identique a avant).
- **`engine/projection.py`** : horloges avancees dans la boucle GL (comme `TextCycle`), fonctionne sans GUI.
- **GUI** : `gui/gui_logo.py` (source Texte : bloc « Vague de lettres » et « Remplissage des lettres », curseurs ci-dessus, grises hors
  source Texte) ; `gui/gl_gui.py` / panneau de controle global : deux cases dans l'onglet *Masters*, branchees sur les memes reglages
  (cocher a un endroit met l'autre a jour, `sync_master_panel`).
- **Hors champ** : source logo image / video, automations (courbes), reactivite audio (la vague est sur horloge libre dans cette version),
  Full Randomizer (ne tire pas ces reglages), telecommande telephone.

## Verifications

- **Pur** (`check_textfx`, TDD) : `ola_alpha` (extinction et rallumage dans l'ordre des rangs, toutes les lettres visibles hors balayage,
  fondu borne, `dur` > `every` borne, `fade` borne a `dur / 2`), `OlaClock` (demarre a l'activation, bord de rallumage une fois par cycle),
  `FillClock` (remise a zero au rallumage, au changement de texte, par periode sans vague, jamais deux fois d'affilee), table des reglages.
- **Rendu** (`check_text_ola`, `check_text_fill`, GL) : lettres eteintes puis rallumees dans l'ordre (texte test sur fond uni), motif
  **contenu dans le masque** (aucun pixel hors des lettres ne change), vague + remplissage cumules et lisibles, cumul avec la fonte acide
  et la rotation, image identique a avant quand les deux sont coupes, carte du rang (une ligne, plusieurs lignes, accents, i / j, espaces).
- **GUI** : cases dans OVERLAY et dans *Masters* synchronisees, reglages dans un preset puis une scene, panique, grisage hors source Texte.
- **Cout** : mesure de `TextFill.step` a la resolution du poste (ms / image par `text_fill_speed`), chiffre ajoute a `EFFECT_COST_MS` et
  a la fiche ; `PerfGuard` reduit les pas.
- **Documentation** : fiche `docs/fonctions/logo.md` (section « Overlay texte : vague et remplissage »), ligne dans l'index de `CLAUDE.md`
  seulement si un theme change.

## Non verifie (demande le materiel)

Rendu et lisibilite au projecteur dans le noir ; le rythme de 3 s de balayage et 20 s de periode a l'echelle d'une vraie soiree ; cout de la
simulation sur le PC de soiree ; videos enregistrees en format vertical regardees a l'oeil.
