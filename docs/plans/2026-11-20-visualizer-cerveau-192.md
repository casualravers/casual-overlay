# Visualizer du « cerveau » (issue #192) : voir en direct ce que l'appli detecte, predit et decide

Brainstorming fait dans le chat le 2026-11-20 (7 series de questions, 3 maquettes). Maquette de reference, animee et interactive :
`docs/plans/maquettes/visualizer-192.html` (a ouvrir dans un navigateur ; donnees simulees, cycle de ~1 min : 12 mesures de groove, 8 de montee,
8 de pic avec le drop au debut, 4 de cassure).

## 1. Pourquoi, pour qui

Aujourd'hui il faut deviner pourquoi l'appli a fait tel flash ou tel changement. Le visualizer montre en direct, en lecture seule :
le tempo detecte et la grille, les frappes (kick, snare, hi-hat), ce que l'appli s'attend a entendre (prediction), les effets declenches et
leur raison, la phase du morceau, ce que les modes auto preparent, et la sante du programme.

Usages : en soiree sur l'ecran du PC portable (coup d'oeil), reglage a la maison, et le montrer a quelqu'un (la vue normale doit etre assez
belle pour etre montree telle quelle). Pas d'usage « apres coup » en v1 (replay hors perimetre).

Critere d'acceptation : je lance un morceau, j'ouvre le visualizer et je comprends d'un coup d'oeil le tempo, ou on en est dans la mesure,
quel effet vient de partir et pourquoi, ce que les modes auto preparent ; lisible dans le noir ; ni le rendu ni le son ne ralentissent.

## 2. Decisions validees

| Sujet | Decision |
|---|---|
| Place | Panneau dans l'interface : un bouton **bascule l'espace de controle global du Mode VJ et le cerveau** (meme emplacement, panneau etendu) |
| Detacher | Bouton « detach » : fenetre a part (Toplevel), position / taille / etat detache retenus d'un lancement a l'autre, option « toujours au-dessus » ; **fermer la fenetre = la raccrocher** |
| REC | Pendant un enregistrement, le panneau est **fige** (plus aucun redessin, mention « fige pendant le REC ») |
| Vues | **Quatre vues**, toutes gardees, choisies par des boutons : **iso**, **mandala**, **tunnel**, **sismo** |
| Rafraichissement | **Page par page** (iso, mandala, sismo) : au debut d'une page, les 4 mesures sont predites d'un coup ; la ligne avance et realise (ou non) les predictions ; a la fin, nouvelle page et la ligne repart du debut. Le **tunnel** defile en continu (les 4 prochaines mesures arrivent du fond) |
| Grille | 4 mesures x 16 pas (4/4), une ligne par instrument : **kick, snare, hi-hat** ; intensite de la frappe = luminosite / hauteur |
| Prediction | Cases **fantomes** (fil de fer) ; probabilite d'un pas = 0,6 x part des 4 mesures de la page precedente qui ont frappe a ce pas + 0,4 x frappe au meme pas une phrase (8 mesures) plus tot ; affiche si > 0,2, « attendu » si >= 0,5 |
| Realisation | Frappe attendue et venue = **realise** (plein) ; venue sans etre attendue = **surprise** (plein, contour blanc) ; attendue et absente = **manque** (croix rouge). Score de la page dans l'en-tete |
| Effets | **Pistes** d'effets : strobe, foudre, glitch, ripple, chroma, wobble, logo, scene ; **fil d'evenements** (4 dernieres lignes) ; **raison au survol** |
| Lignes de cause | Bouton « causes » (actif par defaut) : un trait pointille relie la frappe a l'effet qu'elle a declenche (kick -> ripple, snare -> chroma...) ; le fil affiche `<- kick` |
| Montee / drop | **Voile** anime par-dessus les pistes (lignes de relief qui gagnent en intensite pendant la montee, blanc au drop puis relachement) |
| Phases | **Bande coloree** sous la grille + **teinte de fond** du panneau qui suit la phase |
| Anticipation | Comptes a rebours des fins de cycles 8 / 16 / 32 temps, montee en cours (duree, strobe a venir), prochaine scene VJ et delai, fantomes |
| Jauges | BPM + confiance + origine de la grille ; sources audio par couche (fond / logo : basses, mediums, aigus) ; masters et modes auto (master des effets, phases auto, strobe auto, tempo auto, VJ) ; sante (images/s, charge GPU, latence audio estimee, REC) |
| Sans tempo | Frise libre en secondes avec « tempo non detecte » ; la grille de 16 apparait des que le BPM est cale |
| Interaction | Lecture seule : survol (raison), pause de la vue (l'appli continue), masquer des pistes d'effets |
| Langue | Libelles **en anglais** (kick, snare, hi-hat, build, drop, groove, peak, strobe...) ; **raisons en francais** (sans accents, comme le reste) |
| Style | Neon sur noir, dedie a ce panneau ; couleurs de la maquette (kick #ff3d7f, snare #2ef2c8, hi-hat #ffd23f, strobe blanc, foudre #9d8cff, glitch #ff7a3d, ripple #3db8ff, chroma #ff4fd8, wobble #7dff6b, logo #ffb347, scene #c9d1ff ; phases groove #2ef2c8, build #ffb347, peak #ff3d7f, break #6b8bff) |
| Budget | On **mesure** le cout reel (gels du fil Tk, son pendant un REC) avant de valider ; je choisis le sacrifice selon la mesure |
| Hors v1 | Replay d'une soiree, piloter la simulation depuis le visualizer, vue web / telephone |

## 3. Les quatre vues (detail : la maquette fait foi pour l'allure)

Communs a toutes : en-tete (BPM, confiance, origine de la grille, phase et sa duree, page et mesure, score des predictions, fins de cycles,
prochaine scene), legende, fil d'evenements, survol, pause, causes. Les jauges sont sous la vue, en trois colonnes : sources (barres
fond / logo par bande), auto (pastille allume / eteint + valeur : fx master, phases auto, strobe auto, tempo auto) et sante (images/s, GPU,
latence, REC).

- **iso** : grille 2D en vue isometrique, frise presque carree (une rangee d'instrument est ~4 fois plus large qu'un pas). **Le temps part de
  devant et s'eloigne vers le fond** a mesure que la ligne avance. Plateau des instruments (piliers : hauteur = force) et plateau des effets a
  cote (strobe et foudre en hautes colonnes, scene en dalle, autres en cubes) ; voile en lignes de relief au-dessus des effets ; la ligne du
  present est un plan translucide ; curseur d'angle de camera (18 a 45 degres).
- **mandala** : un anneau par mesure (mesure 1 au centre), un sous-anneau par instrument ; **le 1 pile en haut, 2 / 3 / 4 pile a droite / en
  bas / a gauche** (cases centrees sur les axes : la premiere maquette paraissait desaxee d'un demi-pas) ; couronne de la phrase de 32 temps avec
  crans aux fins de cycles 8 / 16 / 32 ; anneaux des effets autour ; centre = BPM + phase, ondes du voile pendant la montee ; aiguille du present.
- **tunnel** : on avance dans un couloir ; voies kick / snare / hi-hat au sol, pistes d'effets sur les murs (4 a gauche, 4 a droite), voile au
  plafond ; les predictions arrivent du fond en fil de fer et se remplissent en passant le cadre blanc du present ; un eclair du strobe blanchit
  le tunnel. Limite assumee : le passe disparait vite derriere la camera (l'historique reste dans le fil).
- **sismo** : une onde par instrument (passe en trait plein, prediction en pointilles), portee de 8 lignes pour les effets poses comme des
  notes, voile au-dessus de la portee. La plus sobre.

## 4. Architecture (approche A validee)

### 4.1 `core/brain.py` (Python pur, stdlib : se teste partout)

- **`BrainLog`** : journal tournant des evenements, borne (duree ~ 2 phrases, nombre max), protege par un `threading.Lock` (stdlib : permis
  dans core/). Evenement : `(t, family, strength, reason, cause)` ; familles : `kick`, `snare`, `hihat`, les 8 pistes d'effets, `phase`, `build`,
  `drop`. `t` = horloge commune du moteur (la meme que la grille : a fixer au plan d'apres `engine/audio.py`). Publier = un ajout sous verrou,
  aucun calcul.
- **Placement** : `step_of(t, grid)` -> pas de 16e absolu (arrondi au plus proche, depuis l'ancre de `BeatGrid`, 4 pas par temps, 4 temps
  par mesure) ; sans grille calee : `None` (frise en secondes).
- **Prediction** : `predict(hits, page_first, step, row) -> float` (formule du tableau) ; statut `real` / `surprise` / `miss` / `ghost`.
- **`snapshot(now, view) -> dict`** : tout ce qu'il faut pour dessiner, deja calcule : page (premier pas, pas courant + fraction), cellules
  (pas, rangee, force, probabilite, statut), effets (pas, piste, force, raison, cause), phase par pas, voile par pas, en-tete, jauges, fil,
  score. Les vues ne font **que** projeter et dessiner. Un instantane par rafraichissement, copie sous verrou.
- **Causes** : l'evenement d'effet porte la frappe qui l'a declenche quand le moteur la connait (source audio de la couche : basses -> kick,
  caisse claire -> snare, etc.) ; sinon `None` (pas de trait).

### 4.2 Qui publie (sans rien recalculer)

- **Fil audio** (`engine/audio.py`) : kick / snare / hi-hat avec leur force (detections existantes : `KickClassifier`, `edge_snare`,
  frappes aigues), phase, BPM, confiance, origine de la grille.
- **Fil GL** (`engine/render.py`) : strobe (avec la raison de `StrobeBrain` et du limiteur), foudre, drop, montee, effets du fond / logo
  quand leur force depasse un seuil (pas une trace a chaque image), voile.
- **Fil Tk** : scenes, transitions, Full Randomizer, prochaine scene VJ et delai.
- **Sante** : images/s, charge GPU (`PerfGuard`), latence estimee, etat du REC.
- `Session.brain` porte le journal (un seul objet partage).

### 4.3 `gui/gui_brain.py` (panneau)

- Construit une fois (parent persistant, comme l'espace VJ) ; bouton de bascule VJ / brain a cote de l'espace VJ ; bouton detach /
  dock ; Toplevel detache (geometrie et « toujours au-dessus » retenus dans les reglages de salle / l'etat de l'interface).
- Un `Canvas` Tk, **elements crees une fois puis deplaces / recolores** (pas de creation / destruction a chaque image) ; mise a jour
  **incrementale** : une cellule ne change qu'au passage de la ligne ou au changement de page ; seuls la ligne, le voile et les fondus bougent
  a chaque rafraichissement.
- Rafraichissement ~20 Hz (`after`), **aucun** quand le panneau est cache (espace VJ affiche, fenetre reduite) ; fige pendant le REC.

## 5. Cout et mesure (avant de valider)

- Mesures : gels du fil Tk (statistiques d'interface deja en place, `core/uistats.py`), images/s du rendu, et **son pendant un REC** (le
  panneau est fige, mais on verifie aussi juste avant / apres).
- Les quatre vues n'ont pas le meme cout : iso / mandala / sismo se mettent a jour par petites touches ; le **tunnel bouge entierement a chaque
  image** (tout defile) : c'est la vue a risque. Si une vue coute trop, je choisis selon la mesure : baisser sa cadence, raccourcir sa
  profondeur (tunnel sur 2 mesures), simplifier le voile, ou la dessiner hors de Tk (image calculee hors du fil Tk, seulement si les autres
  pistes ne suffisent pas). La decision et les chiffres vont dans la fiche.

## 6. Tests (`tools/check_gl.py`)

- **Purs** (`core/brain.py`, TDD) : placement sur la grille (arrondi, ancre, sans grille -> None), journal borne et thread-safe (ecritures
  concurrentes), prediction (formule, page precedente, phrase precedente), statuts realise / surprise / manque / fantome, score, voile,
  comptes a rebours des fins de cycles, causes, `snapshot` sans tempo (frise en secondes), libelles anglais / raisons francaises.
- **Interface** : bascule VJ / brain, detacher puis fermer = raccrocher, position retenue, fige pendant le REC, aucun rafraichissement
  quand cache, nombre d'elements du canvas constant d'une page a l'autre (pas de fuite), les quatre vues se dessinent sans erreur.
- **Moteur** : les publications du fil audio et du fil GL arrivent dans le journal (rendu synthetique), sans changer l'image.

## 7. Livraison (lots dans l'ordre ; la spec part avec le code dans la PR, comme d'habitude)

1. `core/brain.py` + tests purs ;
2. publications (audio, GL, Tk, sante) + `Session.brain` ;
3. panneau : bascule VJ / brain, detacher / raccrocher, fige en REC, en-tete, jauges, fil ;
4. vue iso (page par page, predictions, causes, voile) ;
5. vues sismo, mandala, tunnel ;
6. mesure du cout, decisions de budget, fiche `docs/fonctions/` (nouvelle fiche `cerveau.md` + ligne dans l'index de CLAUDE.md).

## 8. Non verifie (demande le materiel)

Lisibilite dans le noir sur l'ecran du PC de soiree, justesse des predictions et des causes avec la vraie musique, cout reel sur le PC de
soiree pendant un set et pendant un REC.
