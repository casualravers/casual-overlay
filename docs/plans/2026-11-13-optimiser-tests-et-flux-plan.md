# Optimiser les tests et le flux de developpement : plan d'implementation

> **Pour l'executant** : suivre ce plan tache par tache (`superpowers:executing-plans`, ou `subagent-driven-development` si l'utilisateur le demande). Cases a cocher = avancement.
> Rien ne demarre avant la validation de la spec par l'utilisateur ; les taches 5 et 6 attendent aussi son accord sur les fiches de suppression.

**But** : ramener `complet` de ~24 min a <= 12 min et le retour local a < 1 min, sans perdre une garantie.

**Architecture** : `tools/check_gl.py` gagne des options (`--duree`, `--seulement`, `--groupe`, `--sans-pur`) ; le workflow `check.yml` s'en sert (matrice de groupes, pas de doublon, relance ciblee) ; les tests lourds sont allege un par un avec une fiche et un test de sensibilite ; des outils locaux (`tools/impact.py`, `tools/pr_vert.ps1`) raccourcissent la boucle.

**Technologies** : Python 3.12 (stdlib pour les outils), GitHub Actions (Windows), PowerShell.

**Spec** : `docs/plans/2026-11-13-optimiser-tests-et-flux-design.md` (issue #121).

## Contraintes globales

- Documentation, commentaires, messages en francais **sans accents**. Fichiers existants en CRLF : les garder.
- Jamais de commit sur `main` ; une branche par tache regroupee en PR ; pas de `--admin` ; pas d'auto-merge sans demande.
- **Aucun seuil desserre**, aucune verification supprimee sans fiche validee (spec, section 3).
- Toute nouvelle verification pure s'ajoute a `PURES` (`tools/check_gl.py`) ; `core/` reste stdlib seulement.
- Verification de reference apres chaque tache : `.venv\Scripts\python.exe tools/check_gl.py --pur`, puis la suite complete en arriere-plan (~7 min en local). Les 3 echecs connus de ce poste (`scene: capture sans l'entree audio`, `selecteur Source: le bloc texte remplace le bloc image et ecrit logo_source`, `audio coupe: image identique a la video`) ne sont pas des regressions.

## Points d'attention (tests a ecrire en plus des taches)

Chaque ligne est un test epingle dans la tache qui possede le code :

1. Un `check_*` oublie dans tous les groupes, ou present dans deux : la suite doit le voir (tache 2).
2. `--seulement` avec un motif qui ne correspond a rien : echec explicite, jamais « 0 verification, succes » (tache 2).
3. `--sans-pur` ne doit jamais sauter une verification qui n'est PAS dans `PURES` (tache 2).
4. Le groupe GUI lance seul (sans les groupes qui le precedent) doit passer : l'etat partage est le piege connu (tache 3).
5. Une relance ciblee ne relance que les echecs fragiles, et un echec qui revient reste un echec (tache 3).
6. Une verification allegee qui ne voit plus le defaut injecte est refusee (tache 4, test de sensibilite).

---

### Tache 1 : mesure permanente et fiches

**Fichiers** : modifier `tools/check_gl.py` ; creer `docs/plans/2026-11-13-optimiser-tests-fiches.md`.

**Interfaces** : produit `check_gl.py --duree` (imprime le tableau complet `nom  secondes`, trie) et `durations_table() -> list[tuple[str, float]]`, que le workflow ecrit dans `$GITHUB_STEP_SUMMARY` (tache 3). Deja en place : la ligne « Verifications les plus longues » (#117).

- [ ] Ajouter `--duree` : meme chronometrage que `main()` (decorateur `timed`), tableau complet au lieu des 14 premieres.
- [ ] Fiche par `check_*` dans `...-fiches.md` : nom, secondes (journal CI le plus recent, artefact `journaux-verifications`), ce qu'il garantit, classe (garde / alleger / fusionner / retirer), test de sensibilite prevu. Commencer par les cinq plus chers.
- [ ] Verifier : `check_gl.py --duree` imprime toutes les verifications ; la fiche couvre chaque `check_*` de `main()`.
- [ ] Commit.

### Tache 2 : options `--seulement`, `--groupe`, `--sans-pur`

**Fichiers** : modifier `tools/check_gl.py`.

**Interfaces** : produit `GROUPES: dict[str, tuple[str, ...]]` (noms de fonctions `check_*`) avec trois groupes : `moteur` (GL hors GUI : reaction, labyrinthe, stochastique, foudre, holo, melt, cells, noise, mosaic, render, natifs, sphere, doodles, qr), `gui` (`check_gui` et ses prerequis : scenes, skin, masters, dialogues, gates, manager, params), `reste` (le reste : audio, features, remote, tempo, phase, latence, diagnostics, contrat...) ; `selected_checks(args: list[str]) -> list[str]` (les fonctions a lancer pour `--seulement MOTIF` : sous-chaine du nom, plusieurs motifs possibles ; rend une erreur si aucun ne correspond) ; `main()` lit `--groupe NOM`, `--seulement MOTIF`, `--sans-pur`.

- [ ] Test d'abord (`check_groupes`, dans `PURES`) : chaque `check_*` appele par `main()` est dans exactement un groupe ; `selected_checks(["xyz"])` leve une erreur ; `--sans-pur` n'exclut que les noms de `PURES`.
- [ ] Voir le test echouer, puis ecrire `GROUPES`, `selected_checks` et le traitement des options dans `main()` (l'ordre actuel des appels est conserve a l'interieur d'un groupe).
- [ ] Lancer chaque groupe seul en local (`--groupe moteur`, `--groupe gui`, `--groupe reste`) : la reunion des echecs est celle de la suite complete, aucun echec nouveau.
- [ ] Mettre a jour la section « Verifications » de `CLAUDE.md` (options, groupes) ; commit.

### Tache 3 : workflow CI

**Fichiers** : modifier `.github/workflows/check.yml`.

**Interfaces** : consomme les options de la tache 2. Produit une matrice `complet` (`groupe: [moteur, gui, reste]`), l'etape de relance ciblee et le tableau des durees dans le resume du job.

- [ ] `complet` en matrice sur `--groupe ${{ matrix.groupe }} --sans-pur` ; `fail-fast: false` (un groupe rouge ne cache pas les autres) ; le nom des jobs reste `complet (moteur)` etc. : **mettre a jour la protection / les controles requis** (les trois noms).
- [ ] Relance : si le code de sortie est 3, relancer **uniquement** les verifications fragiles echouees (`--seulement` avec leurs noms) ; un echec qui revient fait echouer le job.
- [ ] Ecrire `check_gl.py --duree` (ou la ligne des plus longues) dans `$GITHUB_STEP_SUMMARY` de chaque groupe.
- [ ] Decision E (si validee) : `on.push` sur `main` ne lance que `pur` et `portable` ; ajouter `schedule` (nuit) qui lance la matrice complete sur `main`.
- [ ] Verifier sur une PR : les trois groupes passent, temps reel du plus long groupe note dans la PR ; comparer a 24 min.
- [ ] Mettre a jour la section « Integration continue » de `CLAUDE.md` ; commit.

### Tache 4 : alleger la famille labyrinthe

**Fichiers** : modifier `tools/check_gl.py` (`check_reaction_sim`, `check_rd_zones`, `check_rd_symmetry`, `check_bg_labyrinthe`).

**Interfaces** : aucune API ; un cache local de simulations dans chaque verification (`sim_cached(key, shape, steps, zone, scale) -> ReactionDiffusion` rend l'etat deja calcule pour une meme cle).

- [ ] Pour chaque verification : lister les simulations identiques refaites (`check_rd_zones` : `sim(1.0, 6000)` x2, `sim(0.8, 6000)`..., `check_rd_symmetry` : 3 x 6000 pas) et les partager entre assertions.
- [ ] **Test de sensibilite** avant et apres : injecter (une a la fois) (a) `rc.VOID_THR_FULL` a 0,95 (plus de vide), (b) `rc.BLOB` = `rc.MAZE` (plus de taches), (c) symetrie retiree de `colonize` ; chaque defaut doit faire echouer l'ancienne **et** la nouvelle version. Resultat note dans la fiche.
- [ ] Images : `check_bg_labyrinthe` a 320 x 180 seulement pour les assertions qui ne comparent pas un pixel exact.
- [ ] Mesurer (`--duree`, local ET journal CI) : cible <= 250 s sur le CI pour la famille ; aucun seuil modifie (diff relu).
- [ ] Commit.

### Tache 5 : alleger les autres verifications cheres (apres validation de l'audit)

**Fichiers** : modifier `tools/check_gl.py` ; completer les fiches.

- [ ] D'apres les fiches validees : `check_foudre_render`, `check_bg_stochastic`, `check_render`, `check_masters`, `check_doodles`, `check_holo`, `check_sphere_mode` (memes methodes : calcul partage, moins d'images, test de sensibilite).
- [ ] Aucune suppression sans accord explicite de l'utilisateur sur la fiche concernee.
- [ ] Mesurer ; commit par verification traitee.

### Tache 6 : boucle locale et flux de PR

**Fichiers** : creer `tools/impact.py`, `tools/pr_vert.ps1` ; modifier `CLAUDE.md`.

**Interfaces** : `impact.IMPACT: dict[str, tuple[str, ...]]` (prefixe de chemin -> verifications) ; `impact.checks_for(paths: list[str]) -> list[str]` (repli : toutes les verifications si un chemin n'est pas connu) ; `pr_vert.ps1 <numero>` (attend les trois jobs, `gh pr update-branch` si la PR est en retard, merge `--squash` seulement si tout est vert, sortie non nulle sinon, **jamais** `--admin`).

- [ ] Test d'abord (`check_impact`, dans `PURES`) : chaque module de `core/`, `engine/`, `gui/` a une entree ; un chemin inconnu rend toutes les verifications ; `checks_for([])` rend la liste vide.
- [ ] Ecrire `impact.py` (usage : `python tools/impact.py $(git diff --name-only origin/main)`), puis `pr_vert.ps1`.
- [ ] Garde-fou `pre-push` **facultatif** (documente, non installe d'office) : `--pur`.
- [ ] Decision G (si validee) : amender la phrase « Pas de worktree » du `CLAUDE.md` (exception : sessions paralleles, un worktree par session).
- [ ] Verifier : `pr_vert.ps1` sur une PR de test ; commit.

### Tache 7 : bilan

- [ ] Relever sur 3 PR reelles : temps reel `complet`, minutes facturees (Billing), temps de retour local ; les comparer aux budgets de la spec (section 5) dans un tableau ajoute a la spec.
- [ ] Mettre a jour `CLAUDE.md` (CI, options, outils) ; fermer l'issue #121 avec le bilan seulement quand l'utilisateur valide.
