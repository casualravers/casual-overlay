# Halo holographique : suivre le contour du sujet, sans devenir un ovale (#240)

Spec validee dans le chat le 2026-11-26. Une seule PR : cette spec, son plan et le code. Fiche a lire : [docs/fonctions/logo.md](../fonctions/logo.md) (halo holographique, `holo_*`).

## Ce que veut l'utilisateur

Le halo doit **suivre les contours de l'overlay en formes concentriques**. Constat d'essai : **quand l'intensite monte** (ce qui arrive tres vite : `holo_intensity` va de 0 a 3 et `holo_react` l'ajoute au kick), le halo **s'ellipsifie et perd la forme du sujet**, pour du texte, une video incrustee ou un logo. Verifie par rendu : l'anneau exterieur, rond ou elliptique, domine l'image et masque les anneaux qui suivent la forme.

Reponses (2026-11-26) : les anneaux **gardent le contour puis s'arrondissent doucement** loin du sujet (pas d'ovale brutal) ; a forte intensite on **plafonne la lumiere des anneaux lointains** ; **aucun nouveau reglage**. Maquette validee dans le chat (halo actuel contre propose, carre / video 16:9 / texte, deux mots cote a cote et sur deux lignes).

## Cause (verifiee par rendu)

`gl_shaders/holo.frag` construit le champ avec l'alpha **flou** du logo a 7 echelles de mipmaps, passe au log. Loin du sujet, les plus gros flous effacent la forme : les lignes d'egale valeur deviennent des cercles / ellipses. `post.frag` (`holo()`) eclaire les franges proportionnellement au gradient de ce champ **x l'intensite** : a forte intensite ces anneaux lointains sont la partie la plus visible. Le fondu radial fixe (`1 - smoothstep(0.30, 0.75, dist)`) n'est pas la cause principale (l'enlever laisse le grand anneau).

## Solution

### 1. Champ = distance au contour (`holo.frag` remplace par deux petites passes)

A la meme basse resolution (1/4, `HOLO_DOWNSCALE`) :
1. **Graines** : texel = graine (sa position) si l'alpha de la couche du logo y est au-dessus d'un seuil bas (`HOLO_SEED_ALPHA` = 0,03, lu a un niveau de mipmap qui moyenne 4 x 4 pixels : un trait fin de 1 px reste une graine). Couche vide = aucune graine.
2. **Jump flooding** (`gl_shaders/holo_jfa.frag`, passes a pas decroissant 2^k, ping-pong de deux textures RG16F, une passe de plus pour corriger les erreurs d'un texel) : chaque texel connait la graine la plus proche, donc sa **distance au contour** `d` (en hauteurs d'ecran) et la **direction** vers lui. Plusieurs formes (deux mots, un logo a trous) : le champ est celui de leur union, aucun traitement special.
3. **Passe finale** (`holo.frag`, meme sortie qu'aujourd'hui, donc `post.frag` presque inchange) : `r = v`, `g, b` = gradient, `a` = densite locale du logo (inchangee : `own`).

### 2. Mapping distance -> v (formules pures, `core/holo.py`, TDD, repliquees en GLSL comme `core/textfx.py`)

- **Portee** `Dmax = clamp(holo_reach x 0,9 x sqrt(l x h), 0,10, 0,60)` (hauteurs d'ecran, `l x h` = rectangle du logo `u_logo_rect`, **rapport l / h plafonne a 3** : ajoute a l'implementation apres un essai de rendu, une ligne de texte plein cadre donnait une portee de 0,6) : un logo compact a une portee proche de celle d'aujourd'hui (`holo_reach` 1 = environ 1,5 x le logo) ; un texte large n'a pas une portee proportionnelle a sa largeur.
- **Arrondi progressif** : `w = smoothstep(0,2, 2,7 x d / Dmax)`-equivalent, soit 0 jusqu'a `0,2 x Dmax` et 1 a `2,7 x Dmax` ; `s = d x (1 - w) + 0,9 x w x dcirc` avec `dcirc = max(|p - u_holo_center| - 0,9 x min(l, h) / 2, 0)`. Pres du sujet les anneaux sont des **copies exactes du contour** ; loin ils tendent doucement vers un cercle (jamais d'ovale decale ni de coupure).
- **v** : `v = 1 - clamp(s / Dmax, 0, 1)` lineaire (`HOLO_CURVE` = 1 : essayee a 1,5 en spec, gardee a 1 pour que les franges restent espacees regulierement), 1 contre le logo, 0 a la portee. Le gradient vient du **vecteur vers la graine la plus proche** (norme 1 pointant vers le logo), plus de differences finies : moins de lectures.
- Le **fondu radial fixe disparait** ; `u_holo_center` reste (derive de la poussiere, centre de l'arrondi lointain).

### 3. Plafond de la lumiere lointaine (`post.frag`, `holo()`)

`I_eff = min(I, 1) + (I - min(I, 1)) x near` avec `near = smoothstep(0, 0,85, v)` et `I = holo_intensity x reaction audio x master` (le plafond s'applique donc aussi au kick). A `I <= 1` : **aucun changement**. A `I = 3`, les anneaux proches (`v` > 0,85) montent a trois fois, ceux de la portee restent a l'equivalent de l'intensite 1. Les franges (`fringe`) et la deformation (`holo_warp`) gardent leur formule ; seul le gain de lumiere est plafonne. La poussiere n'est pas touchee.

## Reglages

Aucun nouveau reglage. `holo_reach` fixe `Dmax` (nouvelle formule, valeur par defaut 1,3 conservee, a recaler au rendu pour garder une portee voisine sur un logo compact). Interface, presets et scenes inchanges.

## Cas limites

- **Couche vide** (pas de logo, texte vide) : aucune graine -> `v = 0`, pas de halo (comme aujourd'hui).
- **Logo anime / video** : le champ est recalcule a chaque image (comme aujourd'hui) ; la video a un alpha plein : le contour est son rectangle, anneaux en rectangles arrondis.
- **Deux mots / deux lignes / logo a trous** : voir passe 2 ; les anneaux de chaque forme fusionnent loin du sujet en un contour unique.
- **Sujet tres petit ou tres grand** : `Dmax` bornee a [0,10 ; 0,60] hauteurs d'ecran.
- **Resolutions** : seuil des graines et constantes exprimes en hauteurs d'ecran (comme `u_holo_lod0`) ; meme rendu a 720 p et 1080 p.
- **PerfGuard** : le cout du halo ne doit pas depasser l'actuel (`EFFECT_COST_MS`, halo 3,3 ms a 1080 p) ; si les passes JFA sont plus cheres que les 7 echelles x 4 prises actuelles, reduire a 8 passes et mesurer. `core/safety.py` est recale sur la mesure.
- **Mode transparent / transitions** : inchanges (le champ ne depend que de l'alpha du logo).

## Fichiers

`gl_shaders/holo.frag` (passe finale), `gl_shaders/holo_jfa.frag` (nouveau), `gl_shaders/post.frag` (`holo()` : plafond), `engine/render.py` (programme et deux textures de ping-pong creees avec `holo_tex`, libres a la reconstruction ; `reload_shaders` les recharge), `core/holo.py` (nouveau, formules pures), `core/safety.py` (cout mesure), `docs/fonctions/logo.md`, `core/CLAUDE.md` et `gl_shaders/CLAUDE.md` (fiches), `tools/check_gl.py`.

## Verifications

- **Pur** (`check_holo`, PURES) : `halo_range` bornee, arrondi nul pres du sujet et monotone, `v` entre 0 et 1 et decroissant, `light_gain(I, v)` = `I` pour `I <= 1`, plafonne a `min(I, 1)` pour `v = 0`, continu en `v`.
- **Rendu** (groupe moteur) : couche vide = pas de halo ; un rectangle large a des anneaux proches dont l'iso-contour garde son rapport largeur / hauteur (pas un ovale plus rond), qui s'arrondissent loin ; deux blocs separes : anneaux separes pres, fusionnes loin ; gradient pointe vers le sujet ; **a l'intensite 3 la luminance de l'anneau lointain reste proche de celle a l'intensite 1 alors que celle des anneaux proches monte** ; a l'intensite 1 le halo d'un logo compact reste proche de l'actuel (ecart borne) ; `holo_on` = 0 : image d'origine.
- **Non verifie (materiel)** : lecture au videoprojecteur (finesse des anneaux, plafond a 3), cout GPU mesure sur la machine du VJ, comportement avec une vraie video.

## Hors de cette PR

Nouveaux reglages (espacement des anneaux, plafond reglable), changement de la deformation ou de la poussiere, halo sur les doodles.
