# Reactivite par effet : le mode auto assigne une source a chaque effet (spec)

Statut : a valider. Demande de l'utilisateur (2026-11-13), validee en conversation : approche A, logo decale du fond, effets au repos selon la phase.

## 1. Probleme

Le visuel bouge constamment : ondulation, onde de choc, aberration et glitch d'une couche recoivent tous la MEME source audio
(`layer_levels`, un vecteur `(bass, high, beat, since)` par couche). Meme en mode auto (`AUTO_TABLE`, phase x couche) tous les effets
d'une couche battent ensemble, et fond et logo aussi des qu'ils suivent la meme musique. On ne voit pas QUEL element de la musique
fait bouger QUOI.

## 2. But et critere de reussite

- Le mode auto donne a chaque effet SA source (une bande de frequences, le kick, les frappes aigues, l'energie globale, l'elan...).
- Hors du pic, un ou deux effets seulement sont actifs en meme temps ; les autres sont au repos ou en derive lente.
- Fond et logo n'ont pas les memes assignations : ils ne battent pas a l'unisson.
- Critere (subjectif, juge a l'oeil sur de la vraie musique) : on voit que le visuel suit la musique, pas qu'il est toujours agite.
- Mesurable : (a) un effet au repos laisse l'image strictement intacte pendant que les autres bougent ; (b) en mode manuel
  (source choisie a la main) l'image est identique pixel pour pixel a celle d'avant.

## 3. Hors perimetre

Les reactions du logo calculees cote CPU (pulsation, tremblement, contour : `beat_l` / `high_l` / `bass_l`), le halo, la fonte, les cellules,
la mosaique, le strobe, la foudre : ils gardent la source de COUCHE (inchangee). Pas de nouveau reglage pour l'utilisateur (le mode auto
doit rester sans bouton). Pas de refonte de `core/phase.py`.

## 4. Conception

### 4.1 Un vecteur audio par effet (shader)

`post.frag` : `layer_fx(tex, fx, center, salt, disp, au)` lit `au` pour 4 effets avec des composantes differentes (ondulation : `au.x`,
onde de choc : `au.w`, glitch : `au.z` / `au.y`, aberration : `au.z`). Il prend desormais `au[4]` : indices 0 ondulation, 1 onde de choc,
2 glitch, 3 aberration ; chaque effet lit les memes composantes qu'avant dans SON vecteur. Uniforms : `u_au_bg[4]`, `u_au_logo[4]`
(`vec4`). Les coups du noise (`u_nz_*`) restent combines comme avant.
Mode manuel (source de couche choisie a la main, ou auto sans phase) : les 4 vecteurs sont identiques au vecteur actuel de la couche, donc
l'image ne change pas.

### 4.2 Tableau auto phase x effet (`core/audio_layers.py`, pur)

`AUTO_FX_TABLE[phase][couche][effet] = (source, dosage)` ; `couche` = `bg` / `logo`, `effet` = `wobble` / `ripple` / `glitch` / `chroma`.
Dosage : `0` = repos (effet a zero, en fondu), `0 < d < 1` = niveau, `"derive"` = mouvement lent independant de la musique
(ondulation et aberration seulement : amplitude fixe faible, phase lente), `1` = plein. Point de depart (a regler a l'oeil) :

| Phase | fond : ondulation | fond : choc | fond : aberration | fond : glitch |
|---|---|---|---|---|
| silence, ambiance | basses, derive | repos | repos | repos |
| groove | basses 0,6 | kick 0,5 | repos | repos |
| montee | mediums 0,7 | repos | elan 0,5 | repos |
| cassure | repos | repos | repos | repos |
| pic | basses 1 | kick 1 | repos | repos |

| Phase | logo : ondulation | logo : choc | logo : aberration | logo : glitch |
|---|---|---|---|---|
| silence, ambiance | repos | repos | derive | repos |
| groove | repos | repos | frappes 0,6 | air 0,5 |
| montee | repos | repos | frappes 0,6 | aigus 0,7 |
| cassure | tout 0,6 | repos | repos | repos |
| pic | repos | repos | kick 1 | frappes 1 |

Regles : jamais plus de deux effets actifs par couche hors du pic ; au pic les 4 effets sont actifs mais repartis entre les deux couches
(fond : ondulation + choc, logo : aberration + glitch). Phase inconnue ou `auto_phase` coupe : comme « tout » (comportement d'avant).

### 4.3 Lissage et fondu

Chaque (couche, effet) a son `SourceMixer` (fondu de 1,5 s quand sa source change) et son dosage glisse (constante de temps 1 s) : un effet
qui passe de repos a actif, ou l'inverse, ne saute jamais. Les niveaux d'un effet sont `layer_levels(source) x dosage lisse` (la composante
`since` de l'onde de choc n'est pas multipliee : au repos elle vaut `NO_KICK`). Retard et lissage de couche (`lag_*`, `smooth_*`,
`LayerFilter`) s'appliquent a chaque vecteur comme aujourd'hui.

### 4.4 Liaison fond / logo (`fx_link`)

Aujourd'hui, lie (`fx_link` = 1, defaut), le logo reprend la source du fond. En mode AUTO, le tableau 4.2 s'applique au logo meme lie :
la liaison reste celle des REGLAGES (intensites, centre des ondes), pas de la source audio auto. Choisie a la main, la source du fond continue
de s'imposer au logo lie (comportement d'avant). Risque : un utilisateur qui comptait sur « lie = meme source » en auto voit le logo
se decaler ; c'est voulu (demande explicite).

### 4.5 Voyant

Onglet *Audio* : « effets : ondulation <- basses | choc <- kick | aberration : repos | glitch : repos » pour le fond puis la meme ligne pour le logo
(`core/explain.py`, `Renderer.sources_now` etendu). Pas de journal des decisions.

### 4.6 Cout

Quatre `layer_levels` + 4 fondus par couche et par image (calcul scalaire, negligeable). Le shader lit 4 vecteurs au lieu d'un : aucun
echantillonnage de texture en plus.

## 5. Tests (`tools/check_gl.py`, pur d'abord)

- Tableau : chaque phase / couche / effet valide ; silence et cassure : aucune source active (sauf derive / logo en cassure) ; hors pic au plus
  2 effets actifs par couche ; au pic les 4 effets sont repartis fond / logo ; phase inconnue = « tout ».
- Dosage et fondu : un effet qui passe de repos a plein monte progressivement (jamais de saut a l'image) ; `since` du repos = `NO_KICK`.
- Pixels : (a) mode manuel = image identique a la version d'avant (comparaison avec un rendu de reference) ; (b) en auto groove, effet « repos »
  = image intacte la ou il agirait seul ; (c) logo decale : un kick agite le fond (ondulation / choc) sans rien faire au logo ; (d) derive : mouvement non nul
  sans son, amplitude bornee.
- Voyant : texte lisible pour chaque phase.

## 6. Non verifie / a regler a l'oeil

Le tableau et les dosages sont une hypothese de depart ; seule de la vraie musique sur le videoprojecteur dit si le visuel « respire ». Prevoir une
passe de reglage apres livraison (constantes en tete de `core/audio_layers.py`).

## 7. Decoupage prevu

1. Tableau + dosage + fondu (pur, tests) ; 2. shader et boucle de rendu (4 vecteurs, mode manuel identique) ; 3. voyant ; 4. documentation CLAUDE.md.
