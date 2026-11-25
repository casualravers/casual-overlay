# Texte : colonisation en vague du remplissage (retouche de #199)

Spec ecrite apres les essais de l'utilisateur (2026-11-25), validee sur maquettes dans le chat (« transparent par defaut, et garde la tuile en cache »). Une seule PR : spec + code. Remplace la partie « Remplissage par reaction-diffusion » de [2026-11-25-texte-ola-remplissage-199.md](2026-11-25-texte-ola-remplissage-199.md) (la vague de lettres « ola » n'est pas touchee).

## Retours de l'utilisateur

1. Le budget explose (la simulation de la version 1 coutait +13,9 ms par image pendant la colonisation).
2. Les lettres doivent etre colonisees **en vague** : la premiere, puis la deuxieme...
3. Le depart de la colonisation est **en bas** de chaque lettre, le motif s'etend **tres vite jusqu'en haut** (3 s au plus).
4. Ensuite, le texte peut etre **transparent sauf les bords**, la colonisation **remplit** les lettres.

## Decisions

| Sujet | Decision |
|---|---|
| Cout | Le motif est calcule **une seule fois** sur une petite tuile periodique, avec l'instant d'arrivee de chaque cellule ; la vague ne fait que le **rejouer** (aucune simulation par image) |
| Tuile | 512 x 512, 2000 pas, forme 1,5 (corail), diffusion x0,20 (motif choisi sur maquette). Calcul par tranches de 8 pas par image (la moitie si le GPU est charge), **mise en cache sur disque** (cle = parametres du motif ; cache illisible ou perime = recalcule) |
| Vague | Temps local `tau = temps - rang x ecart - hauteur x front` : rang = lettre de gauche a droite, hauteur = bas -> haut de la lettre ; la matiere apparait a l'instant d'arrivee de la tuile (fondu 0,18 s) |
| Duree | `text_fill_dur` 2,7 s par lettre (3 s au plus) = front 45 % + croissance locale 55 % ; `text_fill_stagger` 3 s entre la premiere et la derniere lettre ; toute la vague ~6 s puis le motif ne bouge plus |
| Transparence | **Par defaut** (`text_fill_clear` = 1) : lettres transparentes sauf un bord de `text_fill_edge` px (3), la matiere remplit l'interieur ; decochee : lettres pleines assombries par la matiere (`text_fill_contrast`) |
| Remises a zero | Inchangees (rallumage de la vague de lettres, nouveau texte, toutes les N s) ; a chaque fois la vague repart et la tuile est lue avec un **decalage au hasard** : un motif different a chaque cycle |
| Perte assumee | Le motif n'est plus « bloque » par les bords des lettres (il est decoupe par leur forme, sans s'aligner sur les traits) |

## Reglages (portee `look`)

`text_fill_on` (0) ; `text_fill_dur` (2,7 ; 0,5 a 3) ; `text_fill_stagger` (3 ; 0 a 8) ; `text_fill_scale` (1 ; 0,5 a 2 : cellules de tuile par pixel de la texture du texte, 1 = 2) ; `text_fill_contrast` (0,9) ; `text_fill_edge` (3 ; 1 a 8) ; `text_fill_clear` (1) ; `text_fill_every` (20). Supprime : `text_fill_speed`.

## Architecture

- `core/textfx.py` (pur, TDD) : `tile_key`, `tile_batch`, `clean_fill`, `fill_times`, `fill_tau`, `fill_reveal`, `fill_total` ; constantes `TILE_*`, `FILL_*`.
- `engine/textfill.py` : `TextPattern` (calcul par tranches, cache disque, texture RG V + instant d'arrivee) ; `gl_shaders/textfill_step.frag` (Gray-Scott periodique + date d'arrivee).
- `engine/render.py` : `text_rise_map` (hauteur dans la lettre, bande par ligne, accents rattaches) a cote de `text_rank_map` (texture RG8) ; `Renderer._update_text_fill` (tuile, FillClock, uniformes).
- `gl_shaders/scene.frag` (`logo_at`) : `fill_matter`, `fill_inner` (alpha erodee, 8 lectures), mode transparent / plein.
- GUI : curseurs Duree par lettre, Ecart de depart, Finesse, Contraste, Bord, Remise a zero + case « Lettres transparentes (bords seuls) » ; les deux cases du panneau Masters sont inchangees.
- Securite GPU : `EFFECT_COST_MS` 0,5 ms (mesure +0,2 ms a 1080p) ; la preparation de la tuile (+3,7 ms par image pendant ~4 s, une seule fois) n'est pas comptee.

## Verifications

Pur : calendrier (ordre des lettres, bas avant haut, 3 s au plus par lettre, duree totale), tranches de preparation, cle du cache, reglages, presets, scenes, panique, securite. Rendu (`check_text_fill`) : texte comme avant tant que la tuile n'est pas prete, preparation par tranches, cache disque (cree, recharge sans calcul, ignore si la cle change ou s'il est illisible), lettres transparentes sauf le bord au depart, colonisation du bas, premiere lettre avant la derniere, motif fige a la fin, lettres pleines, remise a zero au nouveau texte et decalage different, cumul avec la vague de lettres, logo image intact. `check_text_rank` : hauteur (haut 255 / bas 0, par ligne, accents).

## Non verifie (demande le materiel)

Lisibilite au projecteur dans le noir (motif fin, bords) ; rythme de la vague (2,7 s par lettre, 3 s d'ecart) a l'echelle d'une soiree ; duree de la preparation de la tuile (~4 s au premier lancement) et a-coup d'environ 50 ms a sa toute fin sur le PC de soiree ; videos verticales enregistrees regardees a l'oeil.
