# Projection (fenetre GL, ecrans, charge GPU)

Fiche de [CLAUDE.md](../../CLAUDE.md) (issue #132 : detail deplace tel quel). Fichiers : engine/projection.py, engine/window.py, engine/render.py (PerfGuard), engine/app.py.

- Ordre des moniteurs : premier moniteur **non principal** (comparaison par adresse du
  pointeur glfw, pas par `==`), `--monitor N` pour forcer. Fenetre `undecorated` calee sur
  le rect du moniteur, pas de plein ecran exclusif.
- Le nom dshow (ffmpeg) sert aussi de recherche par sous-chaine cote sounddevice (WASAPI
  prefere) ; `--audio-device` la remplace. Sinon entree par defaut.
- Textures : la video et le logo sont en memoire "haut en premier" ; `scene.frag` travaille
  en coordonnees origine haut-gauche (`p.y = 1 - v_uv.y`) pour les lire, le FBO scene est
  en orientation OpenGL, donc `post.frag` n'a aucun retournement.

- **Fenetre par defaut, plein ecran sur demande** (`resolve_fullscreen` rend toujours `False` sauf `--fullscreen` ; la case
  « Plein ecran » de la GUI demarre decochee). Les anciens defauts (plein ecran des qu'un second ecran existe) recouvraient le poste de travail en plein ecran sur le moniteur principal, curseur
  masque et GUI cachee dessous (signale en usage reel : "je perds ma souris"; F ou Echap
  rendent la main). `hide_cursor()` : le curseur n'est masque qu'en plein ecran **hors**
  moniteur principal.
- **Projection a la demande (`--gui`)** : `start.bat` n'ouvre que la fenetre de reglages ; la fenetre GL (`gl_main`, fil `gl`) part
  du bouton **Demarrer la projection** (barre des scenes, `toggle_projection`) ou de `--projeter`. `Projection(s)` (audio2wave_gl.py,
  `s.projection`) : `start()` ouvre **en fenetre** sauf `--fullscreen` (`resolve_fullscreen`), jette les commandes empilees projection fermee, lance le fil ; `stop()` positionne `s.proj_stop` (la boucle GL sort,
  `gl_main` ne positionne PAS `finished_event` en mode `--gui` : Echap ferme la projection, pas les reglages) ; rouvrable a volonte
  (glfw reinitialise a chaque ouverture). Un echec (`SystemExit`, exception) est dit dans le statut, la GUI continue. **Une seule fenetre a la fois** : `gl_main` prend `_GL_SLOT` (attend l'ancien au plus 3 s, puis renonce avec « La projection est deja ouverte » au lieu d'ouvrir une 2e fenetre par-dessus la GUI) ; le bouton ignore un second declenchement a moins de 0,6 s (double clic). Fermer les
  reglages (`on_close`) positionne `stop_event` et `finished_event`. Commandes (`send` dans `gl_gui`) : projection fermee, `save` ecrit
  `gl_params.json` directement, `reload` / `hud` / `fullscreen` repondent « Disponible quand la projection est ouverte ». Plein
  ecran : `Window.retarget(pick_monitor(...))` a chaque passage en plein ecran (un projecteur branche apres le lancement est
  trouve) ; **la commande `fullscreen=1` (case, preset qui la coche) est refusee sur l'ecran des reglages** (ullscreen_blocked ; l'ecran principal si leur position est inconnue) sans `--fullscreen` /
  `--monitor` (la touche F, elle, reste permise : la fenetre a le focus et Echap rend la main). **Ecran de la projection** : `Projection.start()` releve la position de la fenetre de reglages (`s.gui_point`, via `s.gui_point_fn` du fil Tk) : **le NOM du moniteur** (`engine.window.window_monitor_name`, `MonitorFromWindow` + `GetMonitorInfoW`, compare a `glfw.get_win32_adapter`) et non des coordonnees, car celles de Tk ne sont pas celles de glfw des qu'un ecran est mis a l'echelle (processus non DPI-aware : la GUI semblait etre hors de son ecran, `pick_monitor` prenait alors le premier moniteur = celui des reglages, d'ou un plein ecran PAR-DESSUS la GUI). `s.gui_hwnd` (HWND, connu des l'ouverture) permet au fil GL de relire ce moniteur au moment du plein ecran (`gui_pt()` de `loop`) : la fenetre a pu etre deplacee depuis le lancement ; hors Windows ou sans nom, repli sur le point (x, y). `monitor_contains` accepte un point OU un nom ; **verification apres coup** (`watch_fullscreen` de `loop`, 3 s apres chaque passage en plein ecran) : on relit ce que WINDOWS dit (`window_monitor_name` du HWND de la fenetre de rendu et de celui des reglages) ; si le plein ecran est tombe sur l'ecran des reglages alors qu'un autre existe et sans `--monitor` (`window.fullscreen_misplaced`), il est renvoye sur l'autre (au plus 3 fois) et le journal dit « Plein ecran corrige ». Le journal ecrit aussi « Plein ecran verifie : fenetre de rendu (rectangle) sur <ecran> ; reglages (rectangle) sur <ecran> (maximisee : ...) » : c'est la trace a lire si un plein ecran atterrit au mauvais endroit (un bug vu sur le poste de dev, jamais reproduit en test : la verification est le filet) ;
  `pick_monitor(index, avoid)` choisit un moniteur qui ne le contient pas (la GUI peut etre sur le moniteur non principal) ; s'il n'y
  en a pas, la fenetre s'ouvre sur l'ecran unique, **dans le coin bas droit** (windowed_rect, Window.shared) pour laisser les
  reglages atteignables (`--monitor` explicite l'emporte). Sur un ecran partage ou non, la fenetre est contenue dans le moniteur. Sans `--gui` rien ne change
  (`gl_main` direct). `check_gl.py` : `check_projection` (Projection avec cible et ecrans factices) + bouton dans le test GUI.

- **Reprise automatique de la projection** (`Projection._run`) : une erreur qui tue la fenetre GL (pas `SystemExit`, pas une fermeture voulue :
  `proj_stop` / `stop_event`) relance `gl_main` apres `RESTART_DELAY_S` = 1 s avec les reglages du moment (ils vivent dans `s.params`) ; au plus
  `MAX_RESTARTS` = 3 reprises en `RESTART_WINDOW_S` = 120 s, ensuite le statut dit « trop de reprises : bouton Demarrer pour reessayer ». Verifie
  en vrai : une boucle de rendu qui plante est relancee et retombe a 60 fps.

## Charge GPU : garde-fou et mesure

- **Garde-fou** (`PerfGuard`, audio2wave_gl.py) : une image sur 8 (toutes avec `--stats`) est chronometree par une
  requete GPU ; moyenne lissee comparee au budget d'une image a 60 Hz au plus (un ecran a 144 Hz ne rend pas le budget
  impossible). Au-dessus de 90 % du budget pendant 2 s : message dans la ligne de statut de la GUI (`status["perf"]`,
  en tete) et une fois sur stderr, qui nomme les effets couteux allumes (cellules > halo > fonte) ou conseille
  `--render-size` plus petit ; levee sous 80 % (hysterese, pas de clignotement). Il previent, il ne degrade rien tout seul.
- **Optimisations** (mesure ci-dessus) : le contour lumineux (~25 appels de `logo_at` par pixel) est saute hors du
  rectangle du logo agrandi du rayon du contour (alpha nul au-dela, cellules et fonte comprises) ; une cellule candidate
  ne coute plus qu'un hachage (`chash3`). Un shader de mesure : `--synthetic --stats --max-seconds 13` avec les
  reglages voulus (`load_params` remplacable par un script).
