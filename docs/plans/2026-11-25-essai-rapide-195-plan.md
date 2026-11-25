# Essayer une fonctionnalite plus vite : plan d'implementation (#195)

> **Pour les agents** : SOUS-COMPETENCE REQUISE : `superpowers:executing-plans` (ou `subagent-driven-development`). Les etapes utilisent des cases a cocher (`- [ ]`).

**Objectif** : ajouter trois outils d'essai : un fuzz des reglages (partie pure + partie rendu), une planche de preuves, et une checklist d'essai dans le gabarit de PR.

**Architecture** : `core/fuzz.py` (pur, stdlib) tire des jeux de reglages depuis `core.params_schema` ; `tools/fuzz_reglages.py` les rend avec le vrai `Renderer` et rapporte les echecs avec leur graine ; `tools/planche.py` rend des cas decrits par des recettes (`tools/planches/*.py`) et assemble une planche PNG ; la checklist est une convention (gabarit de PR + `CLAUDE.md`).

**Pile** : Python 3.12, `moderngl` (contexte autonome comme `check_gl.py`), Pillow, numpy ; verifications dans `tools/check_gl.py`.

**Spec** : [2026-11-25-essai-rapide-195.md](2026-11-25-essai-rapide-195.md) (a lire avant : elle fait foi).

## Contraintes globales (regles du projet)

- Documents, commentaires et messages CLI en **francais sans accents**.
- `core/` : stdlib seulement, jamais d'import du moteur, de la GUI, de numpy, GL, PIL (verifie par `check_core_purity`).
- Toute nouvelle verification : son nom dans `SUITE` (sinon `check_groupes` echoue), et dans `PURES` si elle ne demande ni GPU ni fenetre. Une verification absente de `GROUPES` va seule dans le groupe `reste` (voulu : 2 min, jamais `moteur`).
- Un seul `check_gl.py` a la fois (deux lancements simultanes font echouer les tests de mise en page).
- TDD pour `core/` (test d'abord) ; pour les outils, le test est une fonction `check_*` ecrite avant le code.
- Jamais de commit sur `main` ; branche `qualite-essai-rapide-195` ; une seule PR spec + code ; pousser et ouvrir la PR seulement sur demande ; fusion squash seulement CI verte.
- Pied de commit : `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` ; pied de PR : `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- `CLAUDE.md` plafonne a 300 lignes (`check_claude_md`) : on remplace, on n'ajoute pas ; toute fiche de `docs/fonctions/` y est citee.

## Points de vigilance (Review Focus)

1. **Cles non tirables** : chaines libres (couleurs, textes, chemins) et `logo_path` (`None`) ne se tirent pas au hasard ; le fuzz les laisse a leur defaut. Un jeu ne doit jamais contenir de valeur que `validate` refuse.
2. **Listes** : `fx_on`, `fx_int`... sont des listes de longueur fixe dont les bornes s'appliquent par element ; un jeu qui change la longueur est invalide.
3. **Faux positifs de l'image « impossible »** : un jeu valide peut legitimement tout cacher (`logo_opacity` = 0, `overlay_master` = 0, `text_scale` minimal). Le critere ne doit echouer que si le jeu ne cache rien.
4. **Cache disque de l'utilisateur** : le remplissage du texte ecrit un cache (`text_fill_tile.npz`) ; les outils le redirigent vers un dossier temporaire, jamais `~/.audio2wave`.
5. **Reproductibilite** : meme graine = memes jeux sur toute machine (aucune dependance a `hash()` de Python, a l'ordre d'un `set`, au temps).

---

### Tache 1 : `core/fuzz.py` (pur) + `check_fuzz`

**Fichiers**
- Creer : `core/fuzz.py`
- Modifier : `tools/check_gl.py` (ajouter `check_fuzz`, dans `SUITE` et `PURES`)
- Modifier : `core/CLAUDE.md` (une ligne : `fuzz.py` -> fiche outillage)

**Interfaces**
- Consomme : `core.params_schema.TABLE` (`Param.key/default/lo/hi/choices/scope`), `BY_KEY`, `validate(key, value)`, `defaults()`.
- Produit :
  - `draw_params(rng: random.Random, scopes: tuple[str, ...] = ("look",), only: set[str] | None = None) -> dict` : jeu de reglages **complet** (toutes les cles de ces portees, les autres cles absentes) ; chaque cle vaut, au hasard, son defaut, sa borne basse, sa borne haute ou une valeur uniforme dans `lo..hi` (les bornes pesent ~40 % a elles deux) ; chaine a `choices` : un choix au hasard ; liste : chaque element tire comme un nombre (longueur inchangee) ; cle sans borne ni `choices` (chaine libre, `None`) : defaut.
  - `cases(seed: int, n: int, scopes=("look",), only=None) -> list[dict]` : `n` jeux, le jeu `k` utilisant `random.Random(seed * 1_000_003 + k)` (donc chaque cas se rejoue seul).
  - `describe(case: dict) -> str` : `cle=valeur` des seules cles differentes du defaut, triees, ~200 caracteres au plus (`...` au bout).

- [ ] **Etape 1 : ecrire `check_fuzz` (echoue : module absent)**. Assertions (spec, section 2a) :
  - meme graine -> memes jeux (`cases(7, 5) == cases(7, 5)`), graines differentes -> jeux differents ;
  - chaque valeur de chaque jeu passe `ps.validate(key, value)` sans etre corrigee ;
  - toutes les cles retournees ont la portee demandee, et `only={"text_border"}` ne rend que cette cle ;
  - sur 200 jeux, `text_border` prend au moins une fois sa borne basse (0.0) et sa borne haute (8.0) ;
  - une liste (`fx_on`) garde sa longueur (5) dans tous les jeux ;
  - `describe` : jeu egal aux defauts -> chaine vide ; un jeu modifie ne cite que la cle modifiee ; sortie plafonnee a 203 caracteres ;
  - `cases(3, 1)[0] == draw_params(random.Random(3 * 1_000_003))` (un cas se rejoue seul).
- [ ] **Etape 2 : lancer** `.venv\Scripts\python.exe tools/check_gl.py --seulement check_fuzz` : attendu : echec (ImportError `core.fuzz`).
- [ ] **Etape 3 : implementer `core/fuzz.py`** (signatures ci-dessus ; pas de `hash()`, pas de `set` itere sans tri ; ne jamais produire une valeur hors `validate`). Une valeur numerique entiere dans la table (defaut `int`) reste un `int`.
- [ ] **Etape 4 : relancer** `--seulement check_fuzz` : attendu : tout vert. Puis `tools/check_gl.py --pur` (le nom est dans `PURES`), attendu : seul l'echec local connu du poste (« scene: capture sans l'entree audio ») ou rien.
- [ ] **Etape 5 : commit** `core/fuzz.py`, `tools/check_gl.py`, `core/CLAUDE.md` : « Fuzz des reglages : tirage reproductible depuis la table (Refs #195) ».

---

### Tache 2 : `tools/planche.py` + recette `texte-remplissage` + `check_planche`

**Fichiers**
- Creer : `tools/planche.py`, `tools/planches/__init__.py`, `tools/planches/texte_front.py`
- Modifier : `tools/check_gl.py` (`check_planche`, dans `SUITE` seulement)
- Modifier : `.gitignore` (verifier que `output/` est deja ignore, sinon l'ajouter)

**Interfaces**
- Produit (`tools/planche.py`) :
  - `Cas` : `NamedTuple(etiquette: str, reglages: dict, instant: float, zoom: tuple[int, int, int, int] | None)` (zone x0, y0, x1, y1 de l'image finale, agrandie x3).
  - `Recette` : `NamedTuple(nom: str, texte: str, taille: tuple[int, int], base: dict, cas: list[Cas], attente: Callable | None)` ; `attente(renderer, dessiner)` laisse une recette preparer son rendu (ex. attendre la tuile du texte).
  - `lister() -> list[str]` : noms des recettes de `tools/planches/` (un module par recette, expose `RECETTE`).
  - `rendre(recette, sortie: Path, cache_dir: Path | None = None) -> Path` : rend tous les cas (un seul `Renderer`, fond degrade raye synthetique, audio `quiet`), assemble la planche (une ligne par cas, etiquette ecrite dessus avec les valeurs des reglages), ecrit le PNG, rend le chemin. Redirige `engine.textfill.tile_cache_path` vers `cache_dir` (dossier temporaire par defaut) le temps du rendu, puis le restaure.
  - CLI : `python tools/planche.py --liste` ; `python tools/planche.py NOM [--sortie CHEMIN]` (defaut `output/planches/NOM.png`).
- Recette `texte-remplissage` : texte `CASUAL RAVERS`, taille 1920 x 330, cas = ligne du front 0 / 3 / 9 px en lettres transparentes et en lettres pleines, aux instants 1,3 / 2,1 / 3,1 s (les valeurs de la maquette de #199) ; `attente` = dessiner jusqu'a ce que `renderer.text_tile.ready`, puis caler les instants sur `renderer._fill_t0`.

- [ ] **Etape 1 : ecrire `check_planche` (echoue : module absent)** : (a) `lister()` contient `texte-remplissage` ; (b) une recette minimale (texte `AB`, 320 x 120, deux cas sans motif ni tuile, `attente=None`) rendue dans un dossier temporaire ecrit un PNG non vide dont la hauteur vaut `2 * hauteur_de_ligne` et dont la largeur vaut 320 ; (c) deux cas aux reglages differents (`text_scale` 0.3 et 0.9) donnent des lignes d'image differentes ; (d) `tile_cache_path` est restaure apres `rendre` ; (e) un nom de recette inconnu echoue avec le message « recette inconnue : X » (code de sortie 2 en CLI).
- [ ] **Etape 2 : lancer** `--seulement check_planche` : attendu : echec (module absent).
- [ ] **Etape 3 : implementer** `tools/planche.py` (le script ajoute la racine du depot a `sys.path`, comme `build_portable.py`) en reprenant l'assemblage de la maquette de #199 (fond : meme degrade raye ; etiquette en jaune sur bande sombre ; redimensionnement final a 1600 px de large) ; puis la recette `texte_front.py`.
- [ ] **Etape 4 : relancer** `--seulement check_planche`, puis rendre la vraie planche : `.venv\Scripts\python.exe tools/planche.py texte-remplissage` ; l'ouvrir et verifier a l'oeil qu'elle ressemble a la maquette validee (ligne du front visible, deux modes).
- [ ] **Etape 5 : commit** : « Planche de preuves : tools/planche.py et recette texte-remplissage (Refs #195) ».

---

### Tache 3 : `tools/fuzz_reglages.py` + `check_fuzz_reglages`

**Fichiers**
- Creer : `tools/fuzz_reglages.py`
- Modifier : `tools/check_gl.py` (`check_fuzz_reglages`, dans `SUITE` seulement)

**Interfaces**
- Consomme : `core.fuzz.cases`, `core.fuzz.describe`, `audio2wave_gl.Renderer`, `engine.render.render_text`, `engine.textfill.tile_cache_path`.
- Produit :
  - `Echec` : `NamedTuple(graine: int, indice: int, source: str, raison: str, reglages: str)`.
  - `fuzz(graine: int, n: int, depuis: int = 0, sources: tuple[str, ...] = ("texte", "image"), *, injecter: Callable | None = None) -> list[Echec]` : pour chaque jeu `cases(graine, n)[depuis:]` et chaque source, pose le jeu sur un `Renderer` (taille 640 x 360, fond uni gris moyen, logo = texte ou image generee), dessine 2 images dont une 2 s plus tard (temps simule), et releve : **exception**, **erreur OpenGL** (`ctx.error != "GL_NO_ERROR"`), **image impossible** = image entierement uniforme alors que le jeu ne cache rien (`logo_opacity` > 0.2, `overlay_master` > 0.2, `text_scale` / `logo_scale` > 0.2, texte non vide). `injecter(renderer, k)` est un crochet de test : il peut lever pour simuler un echec.
  - CLI : `python tools/fuzz_reglages.py [--graine G] [--cas N] [--depuis K]` ; code de sortie 1 s'il y a des echecs, avec une ligne par echec : `graine=G cas=K source=S : RAISON ; reglages : ...` (rejouable avec `--graine G --cas 1 --depuis K`).

- [ ] **Etape 1 : ecrire `check_fuzz_reglages` (echoue : module absent)** : (a) `fuzz(graine=2026, n=20)` rend `[]` ; (b) avec `injecter` qui leve a `k == 3`, `fuzz` rend un `Echec` d'indice 3 dont `graine`, `source` et `reglages` sont renseignes et `raison` cite l'exception ; (c) rejouer `fuzz(graine, n=1, depuis=3, injecter=...)` redonne le meme echec ; (d) le cache de l'utilisateur n'est pas touche (`tile_cache_path` restaure, aucun fichier ecrit hors temp) ; (e) la duree des 20 jeux est affichee et reste sous 60 s (le test echoue au-dela : on reduit alors `n`).
- [ ] **Etape 2 : lancer** `--seulement check_fuzz_reglages` : attendu : echec (module absent).
- [ ] **Etape 3 : implementer** `tools/fuzz_reglages.py` (un seul `Renderer` par source reutilise d'un jeu a l'autre pour tenir le temps ; le cache du texte redirige vers un dossier temporaire ; `try/except Exception` autour de chaque jeu pour continuer apres un echec).
- [ ] **Etape 4 : relancer** `--seulement check_fuzz_reglages` : attendu : vert. Puis un balayage plus large a la main : `.venv\Scripts\python.exe tools/fuzz_reglages.py --cas 300 --graine 1` ; attendu : soit aucun echec, soit des vrais bugs **a noter** (ne pas les corriger dans cette PR : ouvrir une issue par bug, ou les citer dans la PR ; un faux positif du critere « image impossible » se corrige ici en durcissant le critere).
- [ ] **Etape 5 : commit** : « Fuzz des reglages : rendu de jeux tires depuis la table (Refs #195) ».

---

### Tache 4 : documentation et checklist d'essai

**Fichiers**
- Modifier : `docs/fonctions/outillage.md` (section « Planche de preuves, fuzz, checklist »), `tools/CLAUDE.md` (deux lignes), `.github/pull_request_template.md` (checklist en deux listes), `CLAUDE.md` (une ligne, sans depasser 300 lignes)

**Interfaces** : aucune.

- [ ] **Etape 1 : ecrire la section outillage** : usage de `planche.py` (`--liste`, rendre, envoyer la planche dans le chat avant de pousser), de `fuzz_reglages.py` (graine, `--cas`, `--depuis`), regle « ajouter une recette pour toute PR visuelle », limites (pas de pixels de reference, premier perimetre `look`).
- [ ] **Etape 2 : gabarit de PR** : remplacer la section « A tester en vrai » par deux listes : `## Verifie par Claude` (chaque ligne cite le test ou la planche) et `## A verifier par toi` (oeil, projecteur, micro, telephone) ; garder les cases materiel existantes dans la seconde.
- [ ] **Etape 3 : `CLAUDE.md` et `tools/CLAUDE.md`** : une ligne dans l'index « Outillage » (planche, fuzz, checklist) ; `tools/CLAUDE.md` : `planche.py`, `fuzz_reglages.py` -> fiche outillage.
- [ ] **Etape 4 : lancer** `tools/check_gl.py --seulement check_claude_md` (limite de 300 lignes, fiches citees) : attendu : vert.
- [ ] **Etape 5 : verification finale** : `.venv\Scripts\python.exe tools/check_gl.py --pur`, puis `--seulement check_fuzz`, `--seulement check_planche`, `--seulement check_fuzz_reglages`, `--seulement check_groupes`, `--seulement check_claude_md` ; attendu : tout vert (hors echecs connus du poste, a citer).
- [ ] **Etape 6 : commit** : « Outillage : documentation et checklist d'essai (Refs #195) ».

---

## Fin de travail

- Relecture finale (`requesting-code-review`) avant la PR ; corps de PR selon le nouveau gabarit : deux listes, « Non verifie » explicite (la planche suffit-elle a l'utilisateur sans lancer l'appli ? a juger sur les premieres PR).
- Pousser, ouvrir la PR (`Refs #195`), attendre la CI verte, fusion squash, `git pull --ff-only`, carte de l'issue : « A tester » (a la fusion).
- Rien de ceci ne touche le code de production (`engine/`, `gui/`, shaders) : risque pour l'appli nul.

## Auto-relecture

- **Couverture de la spec** : planche (tache 2), fuzz pur (1), fuzz rendu (3), checklist + docs (4), verifications `check_fuzz` / `check_planche` / `check_fuzz_reglages` (1, 2, 3), ordre de la spec respecte sauf que la planche passe avant le fuzz rendu pour reutiliser vite les scripts de #199 (meme ordre que la spec : fuzz pur, planche, fuzz rendu, docs).
- **Coherence des noms** : `cases`, `draw_params`, `describe` (tache 1) sont repris tels quels en tache 3 ; `rendre`, `lister`, `Recette`, `Cas` (tache 2) ne sont utilises qu'en tache 2.

## Ecarts constates a la realisation

Voir la spec : recette `texte-remplissage`, `Cas` / `Recette` dans `tools/planches/__init__.py`, presence du logo epinglee sur un cas sur deux, `--depuis K` rejoue le prefixe. Tache 1 : le module a ete ecrit avant son test (ordre du plan non respecte) ; le test a ete ecrit ensuite et verifie par mutation (tirage non reproductible -> deux echecs).
