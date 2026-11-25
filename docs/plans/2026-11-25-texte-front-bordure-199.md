# Texte : front d'onde depuis un seul point par lettre, bordure noire (retouche de #199)

Retours de l'utilisateur apres la PR #206 (2026-11-25). Une seule PR : spec + code. Complete [2026-11-25-texte-colonisation-vague-199.md](2026-11-25-texte-colonisation-vague-199.md).

## Retours

1. « Rajoute une fine bordure noire autour du texte. »
2. « Le pattern de reaction-diffusion doit avoir un seul et unique point de depart (en bas de la lettre) et s'etendre tel un front d'onde qui colonise uniformement (plus ou moins) tout l'espace. »

## Decisions

| Sujet | Decision |
|---|---|
| Bordure | Anneau **exterieur** noir, epais de `text_border` texels (defaut **2**, 0 = aucune, 0 a 8), pour le texte seulement (jamais un logo image ni les doodles). Calcule dans le shader (dilatation de l'alpha), **multiplie par la vague de lettres** (elle s'eteint avec les lettres) |
| Point de depart | **Un seul par lettre : le milieu du bas de la lettre** (milieu des colonnes, derniere rangee) ; un accent ou le point d'un i suit sa lettre |
| Front | Un **cercle qui grandit** depuis ce point. La « portee » d'un pixel = sa distance a vol d'oiseau au point de depart, normalisee par la plus grande de la lettre ; il apparait quand le front arrive a sa portee (temps local `tau = temps - rang x ecart - portee x front`) |
| Motif derriere le front | La tuile murie (inchangee, en cache) apparait juste derriere le front, avec un leger decalage local donne par ses dates d'arrivee (front 80 % de la duree d'une lettre, apparition locale 20 %) : colonisation « plus ou moins » uniforme |
| Simulation | **Pas de vraie croissance depuis un germe** : avec ce motif fin le front de Gray-Scott avance de ~0,02 cellule par pas, soit des dizaines de milliers de pas pour couvrir une lettre (le budget explose de nouveau, la finesse serait bornee par la taille de la tuile). Le front est donc geometrique (cercle) et le motif est le motif mur de la tuile |
| Reglages | `text_border` (nouveau). `text_fill_dur`, `text_fill_stagger`, `text_fill_scale`... inchanges ; la duree d'une lettre reste 3 s au plus |

## Architecture

- `engine/render.py` : `_letters` (lettres, rattachement des accents, point de depart) ; `text_rank_map` (rang) et `text_reach_map` (portee du front) en sont derives, `text_rise_map` est supprimee. Uniforme `u_text_border` (epaisseur, taille de la texture).
- `core/textfx.py` : `fill_tau(ft, rank, reach, stagger, front)`, `FRONT_SHARE` 0,45 -> 0,8.
- `gl_shaders/scene.frag` : `text_ring` et son application dans `logo_at` (apres la vague de lettres, avant cellules / mosaique / fonte).
- GUI : curseur « Bordure noire » (onglet Logo, source Texte).

## Verifications

`check_text_rank` (portee : distance au milieu du bas normalisee, 0 au depart quand il tombe sur la lettre, 255 au plus eloigne, chaque ligne a ses departs, accent au bout du front) ; `check_text_fill` (rendu : le bas de la lettre se remplit avant le haut, premiere lettre avant la derniere, etc.) ; `check_text_border` (anneau noir exterieur, epaisseur reglable, interieur inchange, s'eteint avec la vague de lettres, logo image intact) ; `check_textfx` (calendrier, reglages).

## Non verifie (demande le materiel)

Lisibilite de la bordure noire sur le fond reel au projecteur (2 texels ~ 2 px a 1080p) ; allure du front sur des lettres a plusieurs branches (E, M, W : le front est a vol d'oiseau, pas le long de la lettre) ; rythme de la vague.

## Retouche (2026-11-25, apres essai de #207)

- Bordure : lecture du rang a chaque prise de l'anneau (ing_tap) ; avant, l'anneau hors du cadre de la lettre lisait le rang 0 et suivait la premiere lettre.
- Front : ill_reveal(tau, v, growth) : front net, motif identique partout, epaississement par l'age (gain 0,45 -> 1) ; plus de dates d'arrivee tirees de la tuile (apparitions au hasard). FRONT_SHARE 0,6.
- Non verifie : rythme du front sur le projecteur.

## Retouche 2 (2026-11-25, apres #209)

- Empreinte des lettres avant leur arrivee : cartes de rang / portee elargies de 2 px (_MAP_PAD) ; contour et bordure n'apparaissent qu'avec le front.
- Front moins diffus : fondu 0,06 s, gain 0,75 -> 1, FRONT_SHARE 0,8. Non verifie : rendu au projecteur.

## Retouche 3 : ligne du front (2026-11-25)

- Demande : un bord pour le front, plus epais que le trait du motif, scintillant. Apres trois maquettes : ligne franche de 3 px, couleur du texte, sans traine, sans bruit ; scintillement = frisson de luminosite lisse.
- Reglages 	ext_front_edge (3, 0 a 30) et 	ext_front_glint (0,5) ; GUI : « Ligne du front », « Scintillement ». Non verifie : projecteur.
