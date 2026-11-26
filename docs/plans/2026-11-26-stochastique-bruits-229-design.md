# Stochastique : choisir le type de bruit du champ (issue #229) - design

Valide dans la conversation (liste des bruits, liste deroulante avec icones).

## Intention

Faire varier le caractere du motif stochastique en choisissant le bruit du champ qui decide ou les points s'allument, sans toucher au grain
des points (leur repartition et ses petits « defauts » font le charme du motif, #224). L'utilisateur choisit dans une **liste deroulante** ou
chaque entree a une **petite icone** qui montre le rendu.

## Decisions (utilisateur)

- Une seule liste « Champ » remplace les trois boutons Bruit / Vagues / Degrade. Entrees, dans cet ordre : **Taches** (bruit actuel, defaut),
  **Organique** (Perlin), **Cellules** (Worley), **Reseau** (bords des cellules), **Veines** (cretes), **Marbre** (domaine deforme), **Vagues**,
  **Degrade** (existants).
- Icone a cote de chaque nom.

## Decisions (Claude, a corriger si besoin)

- Reglage `bg_sto_field` garde son nom ; valeurs existantes inchangees (`noise` = Taches, `waves`, `gradient`), nouvelles : `organic`, `cells`,
  `network`, `veins`, `marble`. Scenes et presets existants identiques a l'oeil.
- **Champ precalcule** : une passe `gl_shaders/sto_field.frag` calcule le champ BRUT (forme seule, 0..1, derive comprise, quart d'onde des vagues
  au kick) une fois par image dans une texture R16F au quart de la taille de la scene (lissage bilineaire), lue par `stochastic_pattern()` au
  centre de chaque point (unite de texture 5). Le coup de contraste du kick et la densite (basses) restent appliques dans `scene.frag` (pas
  besoin de recalculer la texture pour eux). But : les bruits chers (veines, marbre) deviennent abordables et le motif actuel coute moins.
  Ecart avec le calcul direct : interpolation entre texels du champ, invisible a l'oeil (le champ est lisse a l'echelle des points).
- **Icones** : vignettes des vrais points du stochastique sur chaque champ, rendues une fois avec le shader de l'appli par
  `tools/make_sto_icons.py`, livrees dans `asset/sto_fields/<valeur>.png` (copiees par le portable, comme `asset/`). Aucun cout au lancement.
- **GUI** : `Menubutton` + `Menu` dont chaque entree a son image (`compound="left"`), largeur fixe (rien ne bouge quand le choix change) ; le
  bouton montre l'icone et le nom du choix courant.
- **Normalisation** : chaque bruit est ramene a 0..1 avec une moyenne proche de 0,5, pour que « Densite » garde le meme sens partout.
- Full Randomizer : tire parmi les 8 champs. Securite GPU : `BG_STO_COST_MS` mis a jour avec le cout mesure (pire champ).

## Verifications

- Chaque champ : image differente des autres, densite croissante avec « Densite », couverture a densite 0,5 dans une plage raisonnable.
- Tests existants du stochastique (densite, regularite, resolution, derive, son, douceur) verts avec le champ precalcule.
- Champ inconnu -> Taches. Icones presentes pour chaque valeur ; la liste a 8 entrees avec image, choisir ecrit le reglage, largeur fixe.
- Cout mesure avant / apres par champ.
- **Non verifie** : lisibilite des icones et des motifs au videoprojecteur.
