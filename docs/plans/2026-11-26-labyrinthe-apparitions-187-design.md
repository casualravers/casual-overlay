# Labyrinthe : apparitions des nouvelles cellules (issue #187) - design court

Valide dans la conversation (approche B + option C, realisation 1).

## Intention

Dans la palette labyrinthe (reaction-diffusion), pouvoir empecher de nouvelles taches de surgir n'importe ou : le motif en place grandit
depuis ses bords, sans points isoles qui brouillent la lecture (surtout avec les symetries et les zones vides, #167). Par defaut, rien ne
change (scenes et presets existants identiques).

## Constat (code actuel)

- En cours de route, les nouvelles taches viennent **seulement des kicks** : `Renderer._labyrinth` ajoute `reaction.kick_seeds(k)` (3 germes
  a des positions tirees sur tout l'ecran, en orbite si symetrie) a la file `_rd_pending`, poses par `rd_step.frag` (`u_seeds`, 4 par pas).
- Germes initiaux (`reset`, 40) et relance au changement de symetrie : necessaires (sinon ecran vide), **non touches**.
- Garde-fou (`watch`) : motif eteint = tout re-seme. **Garde** (jamais d'ecran vide), meme apparitions coupees.
- Une tache qui se divise n'est pas un germe : c'est la croissance du motif, elle reste.

## Approches considerees

- **A** case « pas de germes » : simple mais le kick perd son effet principal sur le labyrinthe. Ecartee.
- **B** curseur « Apparitions » 0..100 % : part des kicks qui sement. **Retenue.**
- **C** case « Seulement contre le motif » : un germe ne prend que s'il touche de la matiere existante (bourgeonnement). **Retenue, en plus de B.**
- Realisation de C : **(1) test dans le shader** (8 lectures de V autour du germe, germe jete s'il ne touche rien ; tout sur le GPU, symetries
  inchangees) **retenue** ; (2) relire le motif sur le CPU a chaque kick et placer le germe sur un bord : ecartee (relecture GPU bloquante 2 fois
  par seconde, symetrie a refaire).

## Decisions

- `bg_rd_spawn` (0..1, defaut 1, portee `bg`) : le kick numero k seme si `core.reaction.kick_spawns(k, rate)` (tirage deterministe par kick,
  `rate` = part). 1 = tous les kicks (comme avant, bit pour bit), 0 = jamais.
- `bg_rd_bud` (0 / 1, defaut 0, portee `bg`) : uniform `u_bud` de `rd_step.frag`, applique aux germes poses par `step` (seulement ceux des kicks :
  l'etat initial passe par `reset`). Test : 8 points sur un cercle de `2 x rayon + 2` cellules autour du centre du germe ; le germe est pose si l'un
  d'eux a V > `BUD_LEVEL` (0,1 : la matiere visible est au-dessus de 0,2). Meme decision pour tous les pixels du germe (memes lectures).
- Avec `bg_rd_bud`, `BUD_SEEDS` = 4 candidats par kick au lieu de 3 (une partie tombe dans le vide et est jetee).
- GUI (`rd_box`, onglet fond) : curseur « Apparitions » et case « Seulement contre le motif », apres « Vide ».
- Scenes / presets : reglages de portee `bg`, sauves comme les autres ; anciennes scenes = defauts = ancien comportement.
- Full Randomizer : tire `bg_rd_spawn` dans 0,4..1 et `bg_rd_bud` une fois sur trois (ajoutes en fin de tirage).
- Pas d'automation par defaut (le curseur reste automatisable comme tout reglage numerique).

## Verifications

- Pur (`check_reaction_pure`) : `kick_spawns` (0 = aucun, 1 = tous, 0,5 = environ la moitie, deterministe), nombre de candidats.
- Simulation (`check_reaction_sim`) : bourgeonnement, germe isole dans le vide jete, germe contre une tache pose ; defaut = etat identique bit pour bit.
- Rendu (`check_bg_labyrinthe`) : apparitions 0 = aucun germe de kick dans la file ; defaut inchange.
- **Non verifie** : ressenti au videoprojecteur avec de la vraie musique (motif qui « envahit » l'ecran).
