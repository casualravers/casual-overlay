# Foudre v2 : un reseau vivant (spec)

Date : 2026-10-27. Statut : spec a relire, pas encore planifiee ni developpee. Remplace le **modele** de `2026-10-27-foudre-design.md` (arbre fixe revele par generations) ; l'interrupteur Strobe / Foudre, les reglages, les branchements et la securite de cette premiere spec restent.

## Pourquoi

Premiere version (PR #62) jugee par l'utilisateur sur un vrai ecran :
- trace tres peu ramifie, pas organique du tout ;
- le trace n'evolue pas : aucune nouvelle route, aucune ancienne qui s'efface ;
- ca ne pulse pas du centre vers la peripherie.
Cause : l'arbre etait calcule une fois (`build_tree`) puis seulement revele. Il faut un reseau qui **pousse, se consolide et s'efface** en continu.

## Reference visuelle (3 images de l'utilisateur)

Un arbre blanc tres dense jusqu'aux brindilles ; une mousse verte et des racines givrees : un systeme nerveux ou racinaire **maille** (les branches se rejoignent et forment des alveoles), des troncs consolides qui convergent vers un centre lumineux, une hierarchie nette entre troncs epais et ramifications de plus en plus fines. Comportement voulu : certaines routes se consolident petit a petit, des ramifications de plus en plus fines apparaissent, mouvement de fractale.

## Decisions prises avec l'utilisateur

1. **Le pouls suit les routes** (signal nerveux) : la lumiere voyage le long des branches, globalement concentrique mais organique.
2. **Boucles frequentes** : une pointe qui touche une autre branche s'y soude (reseau maille, alveoles).
3. **Silence ou cassure** : le reseau se retracte puis disparait en quelques secondes (fines branches d'abord, troncs ~6 a 8 s) ; un nouveau reseau nait au kick suivant.
4. Gardees de la v1 : mode alternatif du strobe, couleur au choix + coeur blanc, addition par-dessus fond ET logo, plafond de luminosite, reglages `strobe_kind` / `foudre_amount` / `foudre_reach` / `foudre_color`, compteurs `foudre_drop` / `foudre_clear`, phase du morceau, bouton maintenu, Montee, Drop, PANIQUE.

## Le reseau (`core/foudre.py`, reecrit, stdlib seulement)

Repere inchange : centre = (0, 0), hauteur d'image = 1, largeur = `aspect`. Constantes en tete du module, a regler a l'oeil.

**Noeuds** : position, parent, `dist` (longueur du chemin depuis la racine), cap, `vigor` 0..1, `desc` (nombre de pointes en aval), epaisseur courante `w`, drapeau pointe active (`steps_left`). Un segment = un noeud relie a son parent. Les **soudures** sont des segments supplementaires entre une pointe et un noeud existant (elles s'eteignent avec la pointe). Une grille spatiale (cellules de la taille du rayon d'evitement) donne les voisins sans parcours complet.

**Croissance** : a chaque pas de temps, chaque pointe active avance par pas de `STEP` (0,012) a `STEPS_PER_S` x tempo x (0,6 + 0,4 x energie). Cap = cap + bruit (derive) + biais vers l'exterieur + repulsion des branches voisines (non ancetres) ; bifurcation avec la probabilite `FORK_P` par pas (angle 25 a 60 degres, reste de vie x 0,7) ; une pointe s'arrete quand sa vie est finie ou quand elle sort du cadre x `reach`. **Soudure** : si le nouveau point est a moins de `JOIN_R` d'un noeud qui n'est ni un ancetre proche ni du meme rameau, la pointe s'y soude (probabilite `JOIN_P` 0,85) et s'arrete : c'est ce qui cree les boucles.

**Premier kick (reseau vide)** : une racine au centre (leger decalage) et 6 a 8 troncs a angles irreguliers, longue vie.

**Pouls (chaque kick, au plus toutes les 0,25 s)** : une onde part de la racine et avance en distance de chemin a `PULSE_SPEED` x tempo ; elle porte une energie. Au passage elle **renforce** les noeuds atteints : `vigor += energie x (0,25 + 0,75 x importance)` ou l'importance vient de `desc` (modele des tuyaux : les routes qui alimentent beaucoup de pointes recoivent le plus). Au plus 4 ondes en meme temps. Le kick fait aussi naitre `round(3 + 7 x energie)` **nouvelles pousses** sur des troncs / pointes vigoureux (cap +- 25 a 70 degres, biais vers l'exterieur) : de nouvelles routes apparaissent.

**Consolidation** : `w` tend vers une cible (largeur fine + (largeur tronc - fine) x min(1, sqrt(desc) / reference)) avec une inertie de ~0,5 s : les routes tres utilisees s'epaississent petit a petit, les troncs gagnent en epaisseur a chaque kick, les pousses restent fines.

**Effacement** : `vigor` decroit (`DECAY` 0,18 / s x (1,6 - 0,6 x importance) : les fines partent plus vite ; x 1,5 hors groove). Une pointe sans vigueur est supprimee (feuilles d'abord, jamais un noeud qui a encore des descendants ; la vigueur affichee d'un noeud est le max de la sienne et de celle de ses descendants : un tronc ne disparait jamais avant ses branches). Les routes non renforcees s'effacent donc, les autres durent : le reseau change en permanence.

**Energie (plafond selon la phase, `phase_ceiling` inchange)** : elle regle l'energie des pouls, le nombre de pousses, la cadence de croissance et la densite que le reseau peut tenir (au pic le reseau est dense, en groove moyen, en cassure il se retracte, en silence il disparait). Bouton maintenu = energie 1 en continu (un pouls toutes les 0,5 s, sans clignotement global). **Drop** : une grande onde (energie 1,5) + 24 pousses d'un coup + toutes les vigueurs relevees. `clear()` / PANIQUE : tout est vide.

**Plafonds** : 1200 segments, 100 pointes actives, 60 pas de croissance par image ; au-dela, plus de bifurcation ni de pousse tant que l'elagage n'a pas fait de place. Deterministe pour une graine (`Random(graine + episode x 7919)`), temps injecte.

**API** (inchangee pour le moteur) : `Lightning(seed)`, `configure(aspect, reach)`, `update(dt, kicks, ceiling, hold, speed, high=0.0)`, `drop()`, `clear()`, `empty` ; **nouveau** : `segments() -> list[(x0, y0, x1, y1, largeur, vigueur, dist)]`, `pulses() -> list[(rayon, energie)]`, `stats() -> dict` (segments, pointes, soudures, bifurcations, dist maximale), plus de `gens` / `charge` / `draw_list`.

## Audio (complexe et bien visible)

- **Kick** : pouls + pousses + renforcement.
- **Basses** : l'epaisseur et le halo respirent (shader).
- **Aigus** : plus de brindilles fines naissent (probabilite de pousse fine) et les fines scintillent (shader).
- **Tempo** : vitesse de croissance et de l'onde (tempo 0 = tout fige, rien ne rentre : la croissance est integree).
- **Phase** : plafond d'energie (voir plus haut). Tout x master de reactivite comme avant.

## Rendu

- Les pouls et la vigueur ne sont plus calcules cote CPU image par image : le **shader de sommets** calcule l'intensite a partir de la vigueur, de la distance de chemin, des 4 ondes (`u_pulse[4]` : rayon, energie), des basses, des aigus et du temps ; le CPU ne fournit que la geometrie et ses attributs. Rubans additifs a demi-resolution, coeur blanc + halo (mipmaps), composition, plafond a 0,9, tout comme la v1.
- Sommet = centre du segment (2f), decalage du coin (2f), `across`, vigueur, dist, finesse (6 x 4 flottants x 6 sommets, 1200 segments au plus, reecrit a chaque image tant que le reseau existe).
- Largeur minimale d'un texel, intensite reduite au prorata (comme la v1).

## Interface

Inchangee (radios Strobe / Foudre, Intensite, Portee, Couleur). Le voyant dit « foudre : 312 segments, 14 pointes, pouls 2 » a partir de `stats()`.

## Securite

Aucun flash plein ecran (plafond 0,9, lumiere locale). Le pouls est une bande qui se deplace, pas un eclat global : pas de clignotement du reseau en entier (bouton maintenu compris). Au plus 4 poussees par seconde. La PANIQUE vide tout.

## Verifications

Pures (`check_foudre_pure`, reecrite) :
- deterministe pour une graine, differente d'une graine a l'autre ;
- le reseau grandit avec les kicks et reste sous 1200 segments / 100 pointes sur 60 s de pic ;
- bifurcations reelles, **soudures** (boucles) presentes, rien hors du cadre pour `reach` 1 (tous formats d'image) ;
- modele des tuyaux : le segment le plus proche de la racine est plus epais que ceux des pointes ; les troncs s'epaississent de kick en kick (consolidation) ;
- evolution : sous musique soutenue des segments nes tot disparaissent et d'autres naissent plus tard ;
- effacement : sans kick, les fines disparaissent avant les troncs et le reseau est vide en 8 s ; un tronc ne disparait jamais avant ses branches ;
- pouls : le rayon de l'onde croit a la vitesse voulue, les noeuds proches de la racine sont renforces avant les lointains, au plus 4 ondes ;
- tempo 0 = rien ne bouge, un tempo qui baisse ne fait rien rentrer ; dt infini / negatif / NaN, rafale de kicks, plafond hors plage ;
- cout : 600 images de 16 ms au pic en moins de 20 ms de moyenne (garde contre un calcul quadratique).
Rendu (`check_foudre_render`, adaptee) : image inchangee sans reseau et en mode Strobe ; apres des kicks le reseau est dense (beaucoup plus de pixels allumes qu'avec la v1) ; le **rayon de la bande lumineuse augmente** entre deux images apres un kick (pouls du centre vers la peripherie) ; l'image **change** d'une seconde a l'autre sous musique (evolution) ; couleur du halo ; logo jamais assombri ; formats extremes et redimensionnement ; jamais de flash plein ecran ni de blanc avec trainee ; branchements existants (kick, phase, bouton, montee, drop sans flash blanc, panique dans les deux modes).

## Limites connues

- Le naturel du rendu se juge a l'oeil : pas, derive, rayons de soudure / d'evitement, vitesse du pouls, densite maximale, largeur des troncs seront reglees apres un premier essai (constantes en tete de `core/foudre.py`).
- Le calcul Python du reseau (~1200 segments) coute quelques millisecondes par image au pire : a mesurer sur le poste de dev.
