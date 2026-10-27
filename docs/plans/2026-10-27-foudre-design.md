# Foudre : un strobe subtil en arbre de lumiere (spec)

Date : 2026-10-27. Statut : spec a relire, pas encore planifiee ni developpee.

## But

Ajouter une alternative au strobe : **Foudre**. Sur chaque kick, une decharge part du centre de la projection et s'etale vers la peripherie en ramifications fractales, comme un systeme racinaire lumineux ou une foudre. Chaque kick amplifie la decharge precedente (plus profond, plus fort, plus de branches) jusqu'a un plafond. L'effet doit sembler naturel, respirer avec le son, rester subtil, mais sa reaction a l'audio doit etre complexe et bien visible.

Un interrupteur **Strobe / Foudre** dans l'onglet *Live* choisit lequel des deux est actif.

## Decisions prises avec l'utilisateur

1. **Portee de l'interrupteur** : en mode Foudre, TOUT le strobe devient foudre : le bouton Strobe (maintenu), la case Strobe auto, la Montee et le Drop. Les eclairs blancs ne partent plus. Le curseur « Sombre » n'a plus d'objet (grise).
2. **Couleur** : coeur toujours blanc, halo d'une couleur au choix (defaut bleu electrique).
3. **Melange** : additif, par-dessus le fond ET le logo (comme un eclair reel), au meme endroit que le strobe dans `post.frag` (la symetrie et le kaleidoscope s'y appliquent aussi).
4. **Approche A** : l'arbre est genere en Python pur (`core/foudre.py`, testable sans GPU), dessine par une petite passe GL de segments additifs, halo par mipmaps. Pas de bruit « ridged » tout shader (pas de vraies ramifications), pas de croissance GPU persistante (difficile a maitriser).

## Forme de l'arbre (`core/foudre.py`, stdlib seulement)

- Racine au centre de l'image (leger decalage aleatoire). Cinq a sept branches maitresses sortent a des angles irreguliers, avec un biais de direction tire au hasard : l'etalement est concentrique mais jamais un cercle parfait.
- Chaque segment est une ligne brisee en zigzag (deplacement du point milieu, recursif), comme un vrai eclair.
- Bifurcations : a 20-50 degres, longueur x 0,6 a 0,8 et largeur x 0,65 a chaque generation.
- Portee visee : le bord de l'ecran, avec une anisotropie (certaines directions vont plus loin).
- Coordonnees normalisees (hauteur d'ecran = 1, ratio pris en compte) : independant de la resolution.
- Chaque episode a sa graine : l'arbre est different d'une decharge a la suivante ; deterministe pour une graine donnee (tests).

## Accumulation d'un kick a l'autre

- Un etat **charge** (0 a 1) monte a chaque kick (gain decroissant vers le plafond). Chaque kick : les pointes poussent d'une generation (plus profond), de nouvelles branches naissent, et tout l'arbre recoit un eclat bref (~0,15 s).
- Entre deux kicks l'arbre respire : l'eclat retombe a une braise proportionnelle a la charge. La charge decroit (demi-vie ~2,5 s) ; les fines branches s'eteignent d'abord, depuis la peripherie ; les troncs durent plus longtemps.
- Apres ~4 s sans kick tout s'efface doucement ; le kick suivant fait naitre un nouvel arbre (nouvelle graine).
- Plafonds : profondeur 6, ~300 segments au plus, luminosite maximale bornee. L'effet reste subtil.
- Croissance : un front part du centre et atteint les pointes en ~0,25 s (plus vite quand le tempo est rapide).

## Reactivite audio

- **Kick** : croissance + eclat.
- **Basses** : le halo et l'epaisseur ondulent en continu (respiration).
- **Aigus** : les fines pointes scintillent.
- **Tempo** (`effective_tempo`) : vitesse du front de croissance.
- **Phases du morceau** : le plafond de charge suit la phase : nul en silence et en ambiance, moyen en groove, maximal au pic, en hausse progressive pendant la montee, brindilles seules en cassure. Sans phase connue (Phases auto coupees) : plafond moyen.
- **Bouton Strobe maintenu** : la charge est tenue au maximum. **Montee** (`build`) : le plafond monte avec elle. **Drop** : une grande decharge unique a pleine profondeur (a la place du flash blanc de la cassure, le flash du VJ lui-meme n'est pas modifie).
- **Strobe auto** (`StrobeBrain`) : ses decisions d'eclair (accent du « 1 », kick du pic, drop) deviennent des departs de decharge ; les regles ne changent pas (jamais en silence, ambiance ni cassure).
- Tout est multiplie par le master de reactivite comme le reste.

## Rendu

- Nouvelle passe : les segments (rubans fins, largeur decroissante, extremites adoucies) sont dessines en additif dans une texture hors ecran RGBA16F de resolution reduite (1/2), recreee au redimensionnement.
- Coeur blanc + halo de la couleur choisie ; le halo vient du flou par mipmaps de cette texture (technique du halo holographique).
- Compose dans `post.frag` au meme endroit que `u_strobe` (apres la composition des couches, symetrie / pixelisation comprises, avant la teinte globale), en addition : jamais de fond blanchi.
- Aucune passe, aucune texture ni cout quand le mode est Strobe ou que la charge est nulle. Les segments sont envoyes en un seul tampon (VBO) reecrit quand l'arbre change, pas a chaque image.

## Reglages (`core/params_schema.py`)

- `strobe_kind` : `strobe` (defaut) | `foudre`, portee `room`. Hors presets, scenes et memoires ; sauve avec P comme les autres reglages du strobe.
- `foudre_amount` : intensite, 0,7 (0 a 1.5), portee `room`.
- `foudre_reach` : portee, 1,0 (0,4 a 1,5 ; 1 = jusqu'au bord), portee `room`.
- `foudre_color` : halo, `#3a7bff`, portee `room`.
- La PANIQUE coupe `strobe_auto` comme avant et vide la foudre en cours ; elle ne change pas `strobe_kind`.

## Interface (onglet *Live*, bloc du strobe de `gui/gui_vj.py`)

- Deux boutons radio **Strobe | Foudre** (`strobe_kind`) au-dessus du bouton Strobe.
- Mode Foudre : curseurs Intensite et Portee + selecteur de couleur affiches ; « Sombre » grise (jamais retire : la mise en page ne bouge pas). Mode Strobe : les trois reglages de la foudre grises.
- Le bouton garde son libelle « Strobe » (ne pas changer les textes) ; le voyant `strobe_info` dit « foudre : <raison> ».

## Securite

- Aucun flash plein ecran : lumiere locale, luminosite plafonnee, jamais de blanc sur toute l'image.
- Le limiteur existant (`StrobeLimiter`) s'applique aux departs de decharge (10 par seconde au plus) ; en pratique 4 departs par seconde au plus par la charge elle-meme.
- Securite GPU (`core/safety.py`) : cout estime de la passe ajoute a `background_cost`-like `foudre_cost` (borne, ~1 ms) ; si la charge GPU est haute la scene suivante en mode Foudre reste inchangee (le cout est faible) mais le rapport de charge le nomme.

## Verifications (`tools/check_gl.py`)

Pures, dans `PURES` :
- arbre deterministe pour une graine ; graines differentes = arbres differents ;
- asymetrie reelle (histogramme angulaire non uniforme, centre de masse des pointes non nul) ;
- vrais embranchements (nombre de bifurcations > 0 a partir de la generation 2) ;
- la profondeur croit a chaque kick jusqu'au plafond de 6, jamais au-dela ; segments <= 300 ;
- la charge decroit sans kick ; arbre vide apres ~4 s ; fines branches eteintes avant les troncs ;
- plafond par phase (nul en silence / ambiance, croissant groove -> pic) ;
- `strobe_kind` et les reglages dans le schema, valeurs dans la plage.

Rendu (standalone) :
- mode Strobe : image identique a l'actuelle, pixel pour pixel ;
- Foudre sans kick : image inchangee ; apres un kick : pixels plus clairs, tous dans le cadre, centre plus lumineux que la peripherie proche ;
- l'effet s'ajoute sur le logo (le logo n'est jamais assombri) ;
- profondeur visible : l'etendue lumineuse grandit sur 4 kicks de suite puis plafonne ;
- aucun pixel entierement blanc sur l'image (pas de flash) ; PANIQUE vide la foudre ;
- couleur du halo = reglage ; bouton maintenu = charge au maximum ; Drop = decharge a pleine profondeur ; Strobe auto declenche la foudre sur un pic et se tait en silence.

GUI : radios, curseurs affiches / grises selon le mode, reglages sauves et recharges.

## Limites connues

- Le rendu est juge a l'oeil : intensite, vitesse de croissance et finesse des branches seront reglees apres un premier essai sur ecran.
- Les segments sont recalcules cote CPU a chaque kick (quelques centaines de points) : cout negligeable, mesure a faire.
- Un cadre ou un logo tres lumineux au centre masque la racine (additif sur du clair).
