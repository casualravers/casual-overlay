# Plan : vj-remote-controller (telecommande telephone)

Document de planification destine a un agent Claude (et a la relecture humaine). Il decoupe la telecommande en taches independantes, chacune avec ses fichiers, ses
criteres de fin et ses verifications. **Toute la documentation, les commentaires et les messages sont en francais sans accents** (convention du depot, voir `CLAUDE.md`).

## 1. But

Piloter casual-overlay depuis un telephone, sur le Wi-Fi local, sans avoir l'ordinateur sous la main : une petite page web servie par le programme lui-meme.
Usage cible : le VJ en soiree, d'une main, dans la penombre. Un seul telephone pilote a la fois (decision actee).

Ce n'est PAS un deuxieme programme : le serveur tourne **dans le processus de casual-overlay** (un fil de plus), parle au meme `Session` et reutilise les memes
gestes que les touches F1..F9 / S / K / Retour arriere (file `s.scene_requests` lue par `poll_scene_requests` dans `gl_gui.py`). Nom du module : `engine/remote.py`
(le nom « vj-remote-controller » designe la fonctionnalite et la page).

## 2. Decisions actees (reponses de l'utilisateur)

| Sujet | Decision |
|---|---|
| Serveur | Dans le meme processus que la GUI, **stdlib seulement** (aucune dependance ajoutee), fil dedie |
| Temps reel | **SSE** (Server-Sent Events) pour l'etat, requetes POST pour les commandes |
| Page | Un seul HTML + CSS + JS embarque, **sans framework ni CDN** (pas d'Internet garanti) |
| Reseau | Reseau local prive seulement (192.168/16, 10/8, 172.16/12, lien local) ; tout autre pair est refuse |
| Acces | QR code (adresse + jeton) affiche dans la GUI ; code court de secours ; essais limites |
| Transport | HTTP en clair + jeton aleatoire long + reseau prive ; **les limites sont ecrites noir sur blanc** (section 5) |
| Defaut | **Desactive**, case explicite dans la GUI |
| PANIQUE | **Pas de bouton PANIQUE** sur la telecommande (decision de l'utilisateur) |
| Fonctions | Scenes, VJ (lecture / pause / suivante / retour), Verrou, Strobe maintenu, Montee / Drop, masters de pilotage macro, memoires de masters, Temps 1, texte du logo, voyants en lecture seule |
| Masters | Seulement ce qui sert au pilotage macro et au VJ live : **Reactivite et Effets (intensite globale)** en tete, Overlay, Fond, Tempo, Teinte |
| Apercu | Option, **desactive par defaut**, 1 image toutes les 5 s au plus, jamais si cela coute des images (section 7) |
| Appareils | **Un seul** telephone a la fois |
| Design | Joyeux, goofy, decale, enfantin, fete coloree ; **gros boutons, interface simple** (section 6) |
| PWA | Installable sur l'ecran d'accueil, ecran garde allume pendant l'utilisation |

Hors perimetre (a ne pas faire sans nouvelle decision) : bouton PANIQUE, tap tempo, plusieurs telephones, acces depuis Internet, HTTPS, comptes utilisateurs,
edition des scenes / sets / presets, reglage fin des effets (les onglets de la GUI restent le lieu pour cela).

## 3. Architecture

```
telephone (navigateur)                          casual-overlay (un seul processus)
  page statique  <--- GET /  ---------------    engine/remote.py   (fil HTTP, stdlib)
  EventSource    <--- GET /events (SSE) -----      |  etat : instantane de Session + GUI (dict JSON, lu sans verrou)
  fetch POST     ---- POST /api/... ---------->    |  commandes : file `s.remote_requests` (thread-safe)
                                                   v
                                          gui/gui_remote.py  (fil Tk, tick 100 ms, comme poll_scene_requests)
                                                   |  execute les gestes existants : apply_entry, vj_toggle, vj_advance, back_scene,
                                                   |  toggle_scene_lock, start_build, do_drop, do_one, masters (params), memoires, texte du logo
                                                   v
                                          Session / params / moteur de rendu (inchanges)
```

Regles :
- **Le fil HTTP ne touche jamais Tk ni glfw.** Il lit un instantane d'etat (dict remplace atomiquement) et pose des commandes dans une file ; le fil Tk les execute
  (meme principe que `poll_scene_requests`). Les curseurs de masters ecrivent `params[...]` (relu a chaque image), comme les curseurs de la GUI.
- `core/remote_auth.py` : logique pure (jetons, comparaison en temps constant, limitation des essais, pairs prives, validation des commandes). Stdlib seulement,
  temps injecte, testable partout. `core/` n'importe jamais le moteur ni la GUI (`check_core_purity`).
- `engine/remote.py` : serveur (`ThreadingHTTPServer` ou boucle `selectors`), SSE, fichiers statiques, apercu. Importe `core/` ; ne connait pas la GUI.
- `gui/gui_remote.py` : pont (execution des commandes dans le fil Tk), publication de l'etat, panneau de configuration (case, QR, code, etat de la connexion).
- `gui/remote/` (ou `assets/remote/`) : `index.html`, `app.js`, `style.css`, `manifest.webmanifest`, icones. Embarque par `build_portable.py`.

## 4. API (contrat entre le serveur, le pont et la page)

Tout en JSON UTF-8. Toute requete (sauf `GET /` et les fichiers statiques de la page de connexion) exige l'en-tete `Authorization: Bearer <jeton>`.

| Methode et chemin | Role |
|---|---|
| `GET /` , `/app.js`, `/style.css`, `/manifest.webmanifest` | La page (sans donnees sensibles, jamais d'etat) |
| `POST /api/login` `{code}` | Echange le code court contre un jeton de session (le QR contient deja le jeton : voir section 5) |
| `GET /events` | Flux SSE : evenement `state` a chaque changement utile (au plus 5 par seconde), `ping` toutes les 15 s |
| `GET /api/state` | Meme instantane que `state`, pour la reconnexion |
| `POST /api/scene` `{index}` | Applique l'entree de ce rang du set actif (comme F1..F9) |
| `POST /api/vj` `{action: "toggle"\|"next"\|"back"}` | Lecture / pause, suivante, retour |
| `POST /api/lock` | Bascule le Verrou |
| `POST /api/strobe` `{down: true\|false}` | Strobe maintenu (appui / relache) ; le limiteur de securite du rendu reste applique |
| `POST /api/build` `{action: "start"\|"drop"}` | Montee / Drop |
| `POST /api/master` `{key, value}` | Un master : cles autorisees seulement (liste blanche, valeur bornee par `params_schema`) |
| `POST /api/memory` `{slot}` | Rappelle une memoire de masters (1 a 4) |
| `POST /api/one` | Temps 1 |
| `POST /api/text` `{text}` | Texte du logo (source Texte) ; longueur bornee |
| `POST /api/auto` `{key, value}` | Interrupteurs « intelligents » : `auto_phase`, `strobe_auto`, `bpm_auto` (liste blanche) |
| `GET /preview.jpg` | Derniere capture (404 si l'apercu est coupe), jamais plus d'une generation par 5 s |
| `POST /api/preview` `{on}` | Active / coupe l'apercu pour cette session |

Instantane `state` (lecture seule) : set actif et liste des scenes (rang, nom, active), VJ (en marche, reste), verrou, phase et ses mesures, BPM detecte, voyants
(raison du dernier eclair du strobe, sources par couche), masters courants, interrupteurs, texte du logo, etat de la projection, message de statut, `apercu` (bool).
Le format est decrit une fois dans `core/remote_auth.py` (ou `core/remote_state.py`) et verifie par un test (cles, types).

Codes de retour : 200 ok, 400 commande invalide (jamais d'exception cote serveur), 401 jeton absent / faux, 403 pair non prive, 409 « deja pilote par un autre telephone »,
429 trop d'essais ou trop de commandes. Le corps d'une reponse d'erreur ne contient jamais de trace.

## 5. Securite (le point sensible)

Menaces visees : un voisin sur le meme Wi-Fi, une page web ouverte sur le telephone qui ecrit vers l'adresse locale (CSRF / rebinding), un scan de ports, une erreur
de doigt. Hors menace : un attaquant qui ecoute le Wi-Fi (HTTP en clair : le jeton passe en clair sur le reseau local, **a dire dans la GUI et le README**).

- **Ecoute** : l'interface privee detectee (pas `0.0.0.0` aveugle) ; port par defaut 8765, configurable ; si aucune adresse privee n'existe, le serveur ne demarre pas
  et le dit. Chaque connexion entrante est refusee (403) si l'adresse du pair n'est pas privee.
- **Jeton** : `secrets.token_urlsafe(32)`, genere a chaque activation de la telecommande, **jamais ecrit sur disque**, invalide au redemarrage. Compare en temps constant.
  Le QR encode `http://<ip>:<port>/#k=<jeton>` (le fragment n'est pas envoye au serveur ni garde dans les journaux) ; la page le lit, le garde en `sessionStorage` et l'envoie
  dans `Authorization`. Aucun cookie (donc pas de CSRF classique).
- **Code de secours** : 6 chiffres affiches a cote du QR (pour saisir a la main) ; `POST /api/login` : 5 essais ratés par minute et par pair, puis blocage 5 minutes ; le code change a chaque activation.
- **Un seul telephone** : une connexion valide remplace la precedente (l'ancienne recoit 401 et un message) ; la GUI affiche « telephone connecte depuis 192.168.1.23 » et un bouton **Deconnecter**.
- **En-tetes** : `Host` verifie (adresse locale attendue : parade au DNS rebinding) ; `Origin` refuse s'il est present et different ; `Content-Type: application/json` exige pour les POST ;
  corps limite a 4 Ko ; en-tetes `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy` stricte (pas de script externe).
- **Cadence** : au plus 30 commandes par seconde par session (429 au-dela) ; les curseurs de la page se limitent d'eux-memes (envoi a 15 Hz, derniere valeur gagne).
- **Liste blanche** : aucune commande ne passe une cle libre au moteur ; `master` et `auto` n'acceptent que des cles listees, valeurs bornees (`params_schema`) ; le texte est tronque
  (`scenes.MAX_NAME` pour les noms, 200 caracteres pour le logo).
- **Securite spectacle** : la telecommande n'a aucun moyen de contourner le limiteur de strobe, le Verrou de scene ni la securite GPU ; PANIQUE n'existe pas ici, mais le clavier / la GUI la gardent.
- **Journal** (`core/sessionlog.py`) : activation / coupure, connexion et deconnexion d'un telephone (adresse), essais de code rates, pairs refuses. Jamais le jeton ni le code.
- **Panneau GUI** : case « Telecommande telephone » (desactivee a chaque lancement : reglage `remote_on` de portee `state`, voir T5), QR, adresse, code, etat, bouton Deconnecter, avertissement
  « reseau local seulement, non chiffre ».

## 6. Design de la page : « la fete dans la poche »

Principe : un telephone en main dans le noir, un pouce, des gros boutons. Ambiance **fete foraine / confettis / bonbons** : joyeuse, decalee, un peu enfantine, tres coloree,
sans jamais sacrifier la lisibilite ni la surete (les actions risquees restent lisibles et espacees).

- **Palette** : fond nuit tres sombre (violet profond) pour ne pas eblouir, boutons en couleurs vives saturees (rose bonbon, jaune soleil, turquoise, orange, vert pomme, violet raisin),
  une couleur par famille de commande (scenes, VJ, strobe, montee / drop, masters). Contrastes verifies pour le texte sur chaque couleur (WCAG AA au moins en texte gras).
- **Formes** : boutons tres arrondis, epais, avec une ombre « autocollant » decalee, legere inclinaison aleatoire fixe (-2 a +2 degres) et un petit rebond (squash and stretch) a l'appui.
- **Typo** : police systeme arrondie et lourde (pile `ui-rounded, "Baloo 2", "Trebuchet MS", system-ui`, pas de police a telecharger), titres tres gros, majuscules, mots courts.
- **Decor leger** : confettis / etoiles en CSS pur ou SVG inline, animations lentes. `prefers-reduced-motion` respecte. **Aucune animation ne doit gener la lecture des voyants.**
- **Mascotte / ton** : libelles joyeux et courts, pas de jargon (« ON FIRE » pour la lecture, « BOUM » pour le drop, « PAF ! » pour un eclair), avec toujours le libelle clair en petit dessous
  (« Drop ») pour ne jamais hesiter. Messages d'erreur gentils (« Oups, plus de reseau, je re-essaie... »).
- **Disposition** (portrait, une seule colonne defilante + barre d'onglets basse a 4 gros onglets, zone du pouce) :
  1. **SCENES** : grille de gros boutons (2 colonnes), la scene active en couleur pleine qui pulse doucement, nom en entier sur 2 lignes ; sous la grille, la rangee VJ (⏮ retour, ▶/⏸, ⏭ suivante, 🔒 verrou).
  2. **MACRO** : Reactivite et Effets en **deux enormes curseurs verticaux** (les plus utilises), puis Overlay / Fond / Teinte / Tempo en curseurs horizontaux epais, les 4 memoires en gros pastilles,
     interrupteurs Phases / Strobe auto / Tempo auto.
  3. **BOUM** : Strobe (maintenu, enorme), Montee, Drop (le plus gros bouton de la page, isole en bas, pour qu'on ne le declenche pas par erreur : appui long 0,4 s avec anneau de progression), Temps 1.
  4. **TEXTE & INFOS** : champ du texte du logo + bouton « Envoyer » (et un petit historique des derniers textes), voyants en lecture seule (phase, BPM, mesures de montee, dernier eclair), apercu si active.
- **Etat de connexion** toujours visible en haut (pastille verte « connecte » / orange « je cherche... » / rouge « deconnecte »), nom de la scene et du set, reste VJ.
- **Retour d'action** : vibration courte (`navigator.vibrate`) si disponible, flash de la pastille, jamais de popup bloquante. Aucune confirmation sauf l'appui long du Drop.
- **Accessibilite** : cibles tactiles >= 64 px (les boutons principaux >= 96 px), `aria-label` sur les icones, ordre de tabulation logique, contraste verifie, pas de dependance a la couleur seule.
- **PWA** : `manifest.webmanifest` (nom « Casual Remote », icones, `display: standalone`, orientation portrait), pas de service worker (pas de cache : l'etat est toujours frais ;
  l'ecran reste allume avec l'API Wake Lock quand elle existe, sinon un message le suggere).

## 7. Apercu de la projection (option, sans cout)

Contrainte de l'utilisateur : ne rien degrader. Donc :
- **Desactive par defaut**, active par un bouton de la page et seulement tant que l'onglet est visible (`visibilitychange`).
- **Au plus une capture toutes les 5 s**, et seulement si un telephone la demande. Reutiliser la capture existante (`engine/preview.py`, `s.preview_requests`, traitee juste avant `swap_buffers`
  par le fil GL, reduite en JPEG) ; ne jamais lire le framebuffer plus souvent.
- **Garde-fou** : sauter la capture si `s.status["gpu_ms"]` depasse 80 % de `gpu_budget_ms` (le `PerfGuard` existe) ou si la projection est fermee ; la page affiche alors « apercu en pause ».
- **Mesure obligatoire** (tache T8) : temps GPU et fps de la boucle avec et sans apercu actif, resultat note dans `CLAUDE.md` ; si l'ecart depasse le bruit de mesure, la tache est refusee.

## 8. Taches (ordre et dependances)

Chaque tache = une branche et une PR (`feature/remote-tN-...`, jamais sur `main`), `python tools/check_gl.py --pur` vert avant la PR, suite complete verte avant fusion, `CLAUDE.md`
mis a jour dans la meme PR. Une tache ne depend que de celles qui la precedent dans le tableau. T1, T2 et T6 peuvent se faire en parallele.

| # | Tache | Depend de |
|---|---|---|
| T1 | Noyau pur : jetons, pairs prives, limiteur, validation des commandes | - |
| T2 | Instantane d'etat (contrat JSON) et table des commandes | - |
| T3 | Serveur HTTP + SSE (`engine/remote.py`) | T1, T2 |
| T4 | Pont GUI : execution des commandes dans le fil Tk, publication de l'etat | T2, T3 |
| T5 | Panneau de configuration GUI (case, QR, code, deconnexion, journal) | T3, T4 |
| T6 | Page web (HTML / CSS / JS, design fete) | T2 (contrat d'API) |
| T7 | PWA, portable, integration CI | T6 |
| T8 | Apercu de la projection avec mesure de cout | T3, T6 |
| T9 | Securite : revue, tests d'attaque, documentation, README | toutes |

### T1 - Noyau pur de securite (`core/remote_auth.py`)
- **Contenu** : `new_token()`, `check_token(given, expected)` (temps constant), `is_private(addr)` (IPv4 prives, boucle locale, lien local ; IPv6 `fe80::`/`fc00::/7` ; refuse tout le reste et les adresses mal formees),
  `LoginLimiter(now)` (5 essais / minute / pair puis blocage 5 min, temps injecte), `new_code()` (6 chiffres via `secrets`), `RateLimiter` (30 commandes / s), `validate_command(name, body)` (liste blanche, types, bornes,
  rend la commande normalisee ou une erreur lisible), `Session` d'un seul telephone (nouvelle connexion remplace l'ancienne, revocation).
- **Fichiers** : `core/remote_auth.py`, `tools/check_gl.py` (`check_remote_auth`, ajoute a `PURES`).
- **Fin** : stdlib seulement (`check_core_purity`) ; tests : adresses privees / publiques / IPv6 / mal formees, comparaison en temps constant, limiteur (5 essais, blocage, reprise), cadence, chaque commande valide et invalide
  (cle hors liste, valeur hors plage, texte trop long, types faux), un seul telephone.

### T2 - Contrat d'etat et de commandes (`core/remote_state.py`)
- **Contenu** : `build_state(snapshot_sources)` pur : assemble le dict JSON (voir section 4) a partir de valeurs deja lues ; `COMMANDS` (nom -> schema) partage avec T1 ; `diff(old, new)` pour n'emettre
  que les changements utiles (et limiter a 5 evenements / s) ; version du contrat (`"v": 1`).
- **Fichiers** : `core/remote_state.py`, test `check_remote_state`.
- **Fin** : cles et types verifies, etat stable (memes entrees -> meme JSON), pas de donnee sensible, taille < 8 Ko, aucune entree de la GUI importee.

### T3 - Serveur HTTP + SSE (`engine/remote.py`)
- **Contenu** : classe `RemoteServer(session, auth, state_fn, handler)` ; fil dedie ; routes de la section 4 ; SSE (un seul abonne : un nouveau remplace l'ancien) ; fichiers statiques de la page
  (lus au demarrage, pas de chemin libre : table fixe) ; en-tetes de securite ; refus des pairs non prives ; arret propre (aucun fil residuel) ; port occupe -> message clair, pas de plantage.
- **Fichiers** : `engine/remote.py`, test `check_remote_server` (vrai serveur local sur 127.0.0.1 avec adresse privee simulee par injection).
- **Fin** : 401 sans jeton, 403 pair public, 429 apres 5 codes faux, `Host` / `Origin` / `Content-Type` verifies, corps trop gros refuse, SSE recoit un `state` apres un changement et un `ping`, deux sessions : la premiere
  est revoquee, arret = port libere et fil termine, un client lent ne bloque ni le moteur ni les autres routes, aucune trace dans les reponses d'erreur.

### T4 - Pont GUI (`gui/gui_remote.py`)
- **Contenu** : `install(c)` (meme patron que les autres `gui_*.py`) : lit `s.remote_requests` toutes les 100 ms (planificateur existant `ui_every`), execute dans le fil Tk les gestes **existants**
  (`apply_entry`, `vj_toggle`, `vj_advance`, `back_scene`, `toggle_scene_lock`, strobe presse / relache, `start_build`, `do_drop`, `do_one`, masters, memoires, texte du logo), respecte le Verrou (message
  `scene_locked`), publie l'etat (`build_state`) a 5 Hz au plus. Un master ecrit comme le curseur correspondant (`params`, puis `sync_master_panel` pour que la GUI suive).
- **Fichiers** : `gui/gui_remote.py`, branchement dans `gl_gui.py`, `engine/session.py` (file `remote_requests`), test GUI `x_remote_bridge` dans `check_gl.py` (faux serveur, commandes injectees).
- **Fin** : chaque commande a le meme effet que le geste de la GUI (memes assertions que les tests existants), refus propre en Verrou, jamais d'appel Tk hors du fil Tk, un commande qui leve une exception est
  journalisee et n'arrete pas le pont, la GUI reste fluide (`core/uistats` : pas de nouvelle operation > 80 ms).

### T5 - Panneau de configuration (dans l'onglet *Affichage* de l'espace de controle)
- **Contenu** : case « Telecommande telephone » (**decochee a chaque lancement**, reglage `state` : ni sauve ni dans les presets), adresse et port, **QR code** (genere sans dependance lourde : petit encodeur
  QR en Python pur dans `core/qrcode.py` OU Pillow + algorithme fourni, au choix de l'agent apres comparaison, stdlib de preference), code a 6 chiffres, etat « en attente / telephone connecte 192.168.x.x », bouton
  Deconnecter, avertissement « reseau local seulement, non chiffre », message si aucune adresse privee. Journal de session pour chaque evenement.
- **Fichiers** : `gui/gui_remote.py`, `core/qrcode.py` (si ecrit), `core/params_schema.py` (`remote_on`, scope `state`), palette Ctrl+K (« Telecommande : afficher le QR »).
- **Fin** : le QR decode bien (test avec un decodeur de reference ou verification des modules par re-encodage), cocher demarre le serveur, decocher l'arrete (port libere), le jeton change a chaque activation,
  hauteur de la fenetre inchangee (< 700 px), icones et habillage `skin` appliques, aucune erreur Tk.

### T6 - Page web (`gui/remote/`)
- **Contenu** : la page de la section 6, vanilla JS (modules ES, pas de build), `EventSource` avec reconnexion automatique et pastille d'etat, `fetch` avec `Authorization`, curseurs a 15 Hz avec
  derniere valeur gagnante, Drop en appui long, vibration, Wake Lock, `prefers-reduced-motion`, mode paysage correct, aucun appel externe. Strictement la liste des commandes de la section 4.
- **Fichiers** : `gui/remote/index.html`, `app.js`, `style.css`, `manifest.webmanifest`, icones SVG/PNG.
- **Fin** : fonctionne sur Chrome Android et Safari iOS (verification manuelle notee dans la PR, captures d'ecran jointes), cibles >= 64 px, contraste AA verifie (script ou outil, resultat joint),
  aucune requete hors de l'origine, CSP respectee sans `unsafe-inline` (styles et scripts en fichiers), poids total < 150 Ko, test de fumee automatise (Python : charge la page, verifie les ids attendus et que chaque
  commande de l'API a un bouton).

### T7 - PWA, portable, CI
- **Contenu** : `build_portable.py` embarque `gui/remote/` et `core/remote_*.py` / `core/qrcode.py` (`APP_FILES` / `check_portable_modules`), manifest et icones, job `portable` vert, `pur` contient les nouveaux tests.
- **Fin** : le dossier portable demarre la telecommande (verifie par le script de fabrication : demarrage du serveur sur boucle locale + requete `GET /`), CI verte.

### T8 - Apercu sans cout
- **Contenu** : section 7 ; route `GET /preview.jpg`, `POST /api/preview`, page : bouton « Voir l'ecran », rafraichissement toutes les 5 s au plus, visibilite de l'onglet.
- **Mesure** (`tools/mesure_remote.py`, resultat dans le resume de la PR et `CLAUDE.md`) : fps et `gpu_ms` moyens sur 60 s en `--synthetic`, apercu coupe / actif ; ecart accepte : dans le bruit de mesure.
- **Fin** : garde-fou GPU teste (apercu saute quand `gpu_ms` > 80 % du budget), projection fermee -> « apercu en pause », une seule capture en cours, aucune ecriture disque inutile (fichier temporaire reutilise ou memoire).

### T9 - Revue de securite et documentation
- **Contenu** : `tools/check_gl.py` : `check_remote_security` (scenarios d'attaque : jeton absent / faux / rejoue apres revocation, pair public, `Host` etranger, `Origin` etranger, JSON enorme, chemin `..`, 31 commandes en 1 s,
  texte de 10 Mo, cle de master inconnue, injection dans le texte du logo), revue manuelle de la liste de la section 5, README (section « Telecommande » : activer, scanner, limites de securite, depannage pare-feu Windows),
  `CLAUDE.md` (architecture, API, tests). Sur Windows, documenter la regle de pare-feu (profil Prive) et ce que fait la premiere activation.
- **Fin** : tous les scenarios refuses avec le bon code, aucune trace dans les reponses, documentation relue.

## 9. Consignes a l'agent qui realise le plan

1. Lire `CLAUDE.md` en entier avant de commencer (regle de dependance `core/` -> moteur -> GUI, facade `audio2wave_gl`, tests, portable) et `docs/PLAN_VJ_REMOTE.md` (ce fichier).
2. **Nouvelle branche par tache depuis `origin/main`**, jamais de commit sur `main`, jamais `git commit -a` (laisser hors des commits les fichiers deja modifies ou non suivis du dossier `asset/`), `git add` de chemins precis.
3. Reutiliser les gestes existants ; ne rien reecrire de la logique VJ. Si un geste n'est pas appelable depuis le pont, l'extraire proprement (sans changer son comportement) et le dire dans la PR.
4. Tout nouveau reglage = une ligne dans `core/params_schema.py` (portee, plage, doc). Tout nouveau module `core/` reste pur ; tout nouveau fichier est dans le portable.
5. Ecrire les tests AVANT ou avec le code ; ajouter les verifications pures a `PURES`, les tests de mise en page fragiles a `FRAGILES` / `SCREEN_DEPENDENT` si besoin.
6. Documentation, commentaires et messages en francais **sans accents**. Libelles de la page : accents autorises dans la page web (UTF-8), pas dans le code Python.
7. Chaque PR : description courte, ce qui est verifie, ce qui ne l'est pas (materiel, telephone reel), `check_gl.py --pur` et suite complete verts. **Ne pas pousser ni fusionner sans demande explicite de l'utilisateur.**
8. Ne jamais ajouter de bouton PANIQUE, de tap tempo, de multi-appareils ni d'acces Internet : decisions actees.

## 10. Risques et questions ouvertes

- **Pare-feu Windows** : la premiere ecoute declenche une invite ; le profil « Public » bloque. A documenter et a tester sur le PC de la soiree.
- **Wi-Fi d'une salle** : certains reseaux isolent les clients (« AP isolation ») : le telephone ne verra pas l'ordinateur. Prevoir un point d'acces du telephone (partage de connexion) en secours, documente.
- **Latence** : une commande doit etre percue < 150 ms sur un reseau local ; mesurer (T9) et noter.
- **QR sans dependance** : si un encodeur pur est trop long a ecrire, autoriser Pillow (deja requis) plus une table de generation ; decision de l'agent, justifiee dans la PR.
- **iOS** : le Wake Lock n'existe qu'a partir d'iOS 16.4 ; le dire dans la page.
- **Decision a confirmer** : ce plan place la telecommande **dans le depot casual-overlay** (meme processus). Si l'utilisateur prefere un depot separe `vj-remote-controller` (par exemple pour
  versionner la page a part), seules T6 et T7 changent (la page devient un paquet statique servi par le programme).
