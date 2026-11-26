# Stochastique : points qui naissent et meurent en fondu (issue #223) - design court

Valide dans la conversation.

## Intention

Sur la palette stochastique, un point ne s'allume plus d'un coup quand le champ passe son rang : son rayon grandit et diminue en douceur autour
du seuil. But : plus de gresillement aux bords des zones quand le champ derive. 0 = rendu actuel, cout GPU quasi nul.

## Constat

`stochastic_pattern()` (`gl_shaders/scene.frag`) : un point est allume si `f >= rank` (tout ou rien), son rayon ne depend que de la variation
et des basses. Le scintillement des aigus change le rang par epoques : voulu, non touche (dans la zone de fondu il fait varier la taille).

## Approches

Une seule sensee : multiplier le rayon par une rampe autour du rang. (Fondre l'opacite au lieu du rayon : un point pale au lieu d'un point
petit, contraire a « le rayon grandit » ; ecarte.)

## Decisions

- `bg_sto_soft` (« Douceur », 0..1, defaut 0, portee `bg`), uniform `u_sto_soft`.
- Shader : largeur `l = 0,15 x douceur` ; point ignore si `f < rank - l` ; rayon x `smoothstep(rank - l, rank + l, f)` quand `l > 0`.
  A 0 : test `f < rank` et rayon inchanges (image identique bit pour bit).
- GUI : curseur « Douceur » dans le bloc stochastique, sous « Densite ».
- Full Randomizer : `bg_sto_soft` dans 0..0,8, tire en fin de tirage stochastique (les tirages existants ne bougent pas).

## Verifications

- `check_bg_stochastic` : douceur 0 = image identique ; douceur 1, champ qui derive : moins de pixels qui basculent fond <-> point d'une image
  a l'autre (seuil de saut) ; densite moyenne proche ; reglage borne, scene, Randomizer.
- Cout mesure avant / apres (mediane de `draw`).
- **Non verifie** : rendu au videoprojecteur.
