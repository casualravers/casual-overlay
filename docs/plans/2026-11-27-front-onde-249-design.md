# Front d'onde du stochastique : courbe, sans rotation automatique (#249)

Spec a valider dans le chat. Une seule PR : cette spec, son plan et le code. Fiche a lire : [docs/fonctions/fond.md](../fonctions/fond.md) (stochastique, texture « Pavage », #220).

## Ce que veut l'utilisateur

Le front de la vague du pavage (celui qui balaye l'image, derriere lequel le pattern de reaction-diffusion pousse, devant lequel l'ancien reste puis est efface) est **trop robotique** : une ligne droite qui, en plus, **pivote toute seule**. Il veut qu'il soit **courbe**, si possible **parametrable** : cercle qui s'agrandit du centre, arc venant d'un cote, motif organique parti du centre ou d'un cote.

Reponses du 2026-11-27 :
- **Rotation** : le front a son **propre angle, fixe** (non automatise par defaut) ; le degrade Duo garde son angle et son automatisation ;
- **Formes** : droit (comme avant), cercle, arc, organique ;
- **Reglages** : forme + origine + courbure + irregularite.

## Cause de la rotation (verifiee dans le code)

`sto_texture` (`gl_shaders/scene.frag`) oriente le front avec `u_bg_angle`, c'est-a-dire le reglage **« Angle du degrade »** (`bg_angle`). Or son automatisation est **active par defaut** (`AUTOMATION_SPECS["bg_angle"]` : sinus de 53 s, 20 a 160 degres) : l'angle du front tourne donc tout seul, sans que l'utilisateur ait choisi quoi que ce soit.

## Principe : le front = une distance a une origine

Aujourd'hui : `pos` (0 = bord de depart, 1 = bord d'arrivee) est la projection du pixel sur une direction. Le temps local d'un pixel reste `tau = age - pos x duree_du_front` : **seule la facon de calculer `pos` change**, rien d'autre (duree du front, croissance, vague precedente, synchro BPM / mesures inchangees).

| Forme | `pos` |
|---|---|
| **Droit** | projection sur la direction, formule actuelle (sans rotation automatique) |
| **Cercle** | distance au **centre**, normalisee de 0 (centre) a 1 (coin le plus eloigne) : un anneau qui grandit |
| **Arc** | distance a un point d'origine place **hors de l'ecran, du cote d'ou vient la vague** (selon la direction), normalisee entre le point le plus proche et le plus eloigne de l'ecran ; la **courbure** regle le rayon de l'arc |
| **Organique** | la distance du cercle (origine « centre ») ou de l'arc (origine « cote »), **deformee par un bruit lent** : un front irregulier, qui ne revient jamais a l'identique d'une vague a l'autre |

- **Courbure** (0..1) : 0 = presque droit (origine tres loin), 1 = tres arrondi (rayon de l'ordre de la hauteur de l'image). Pour l'arc ; pour l'organique avec origine « cote » ; sans effet pour le cercle (il est defini par le centre).
- **Irregularite** (0..1) : amplitude du bruit sur `pos` (jusqu'a ~25 % de la course a 1). Le bruit change a chaque vague (meme graine que le decalage de la tuile, `epoch_offset`), reste fixe pendant une vague (le front ne frissonne pas).
- **Origine** (Centre / Cote) : ou part la vague organique ; le cote suit la direction.
- **Direction** (0..360) : d'ou vient la vague (droit, arc, origine « cote »). Fixe, non automatisee.
- Une grille de `pos` non monotone (bruit fort) est voulue : le front devient dechire, ce qui est le but ; `pos` reste borne a 0..1.
- Formules **pures dans `core/stotex.py`** (`front_geometry(shape, origin, angle, curve, aspect)` -> point d'origine et bornes de distance ; `front_pos(...)` -> `pos`), repliquees dans `scene.frag` ; les uniforms sont prepares cote Python comme pour le masque RD (#178).

## Reglages (table `core/params_schema.py`, `scope="bg"`)

| Cle | Defaut | Plage | Role |
|---|---|---|---|
| `bg_sto_front` | `straight` | `straight` / `circle` / `arc` / `organic` | forme du front |
| `bg_sto_front_angle` | 0 | 0 .. 360 | direction d'ou vient la vague |
| `bg_sto_front_curve` | 0,5 | 0 .. 1 | courbure de l'arc |
| `bg_sto_front_rough` | 0,5 | 0 .. 1 | irregularite (organique) |
| `bg_sto_front_origin` | `center` | `center` / `side` | origine de la vague organique |

Aucune automatisation par defaut (et pas de courbe de rotation). Enregistres avec **scenes, presets de fond** (`BG_KEYS`) et le **Full Randomizer** (stochastique avec pavage : forme tiree parmi les quatre, reglages dans leurs plages, tires apres les autres).

**Compatibilite** : defaut `straight` = meme front qu'avant, mais **la direction est `bg_sto_front_angle` (0 par defaut) et non plus `bg_angle`** : une scene enregistree avec un autre angle de degrade voit son front partir de la gauche au lieu de cet angle. A noter dans la fiche.

## GUI (`gui/gui_fond.py`, bloc stochastique `sto_box`, visible avec la texture « Pavage »)

Radio « Front de la vague » (Droit / Cercle / Arc / Organique), puis « Direction du front », « Courbure », « Irregularite », « Origine » (Centre / Cote). Grises selon la forme (`gui_gates`, lignes cherchees dans `sto_box`) : direction = droit, arc, organique-cote ; courbure = arc, organique-cote ; irregularite et origine = organique. Tout le groupe comme « Intervalle des vagues » : seulement avec le pavage. Les boutons ne bougent pas (pas de texte variable, lignes toujours presentes).

## Cas limites et risques

- **Format d'image** (carre, vertical, enregistrement) : les distances se calculent en unites isotropes (hauteur = 1) ; normalisation sur les quatre coins du cadre courant, donc le front traverse toujours tout le cadre.
- **Rayon de l'arc a courbure 0** : origine tres eloignee (borne) : proche du droit, sans division par zero.
- **Organique et motif tres detaille** : le bruit deforme `pos` seulement (pas la tuile) ; aucun effet sur le motif lui-meme.
- **Cout GPU** : un `length` de plus dans le chemin « pavage » ; le bruit (2 lectures de hachage) seulement en organique ; a mesurer a 1080 p (attendu < 0,1 ms).
- **Onde sonore** (lignes d'oscilloscope) : pas de front, inchangee.

## Fichiers

`core/stotex.py` (formules), `core/params_schema.py`, `core/randomizer.py`, `gl_shaders/scene.frag` (+ uniforms), `engine/render.py` (uniforms), `gui/gui_fond.py` + `gui/gl_gui.py` (grisages), `docs/fonctions/fond.md`, `tools/check_gl.py`.

## Verifications

- **Pur** : `front_pos` : droit = formule d'avant ; cercle = distance au centre, 0 au centre et 1 au coin ; arc : 0 du cote de depart, 1 a l'oppose, monotone le long de la direction, plus courbe quand la courbure monte ; organique : 0 d'irregularite = cercle / arc exact, borne 0..1, change avec l'epoque, deterministe ; format carre / vertical ; reglages, bornes, aucune automatisation.
- **Rendu** : forme `straight` angle 0 = image d'origine ; cercle : une vague a mi-parcours revele un disque centre ; arc : le bord revele est courbe, du bon cote ; organique : bord irregulier ; la direction ne change plus avec l'automatisation de `bg_angle` ; le shader concorde avec la fonction pure (echantillons) ; scene / preset enregistre et recharge ; Randomizer.
- **GUI** : lignes presentes, grisages, ecriture des reglages.
- **Non verifie (materiel)** : lecture au videoprojecteur, impression « moins robotique » en musique.

## Hors de cette PR

Front anime (qui change de forme pendant la vague), formes dessinees ou image, front pour l'onde sonore, reaction au son du front.
