# Labyrinthe : apparitions des nouvelles cellules (#187) - plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (execution native choisie). Steps use checkbox (`- [ ]`) syntax.

**Goal:** curseur « Apparitions » (part des kicks qui sement) et case « Seulement contre le motif » (bourgeonnement) pour le labyrinthe.

**Architecture:** decision par kick dans `core/reaction.py` (pur), file des germes dans `Renderer._labyrinth`, test de contact dans `rd_step.frag`
(uniform `u_bud`). Defauts = ancien comportement bit pour bit.

**Tech Stack:** Python 3.12, moderngl, GLSL 330, Tk.

**Spec:** `docs/plans/2026-11-26-labyrinthe-apparitions-187-design.md`

## Global Constraints

- Docs, commentaires, messages : francais sans accents.
- `core/` : stdlib seulement (`check_core_purity`).
- Defauts : `bg_rd_spawn` = 1.0, `bg_rd_bud` = 0 -> simulation et image identiques bit pour bit a avant.
- Nouvelle verification : nom dans `SUITE` (et `PURES` si pure) de `tools/check_gl.py`.
- Germes initiaux, relance de symetrie et garde-fou `watch` : inchanges.

## Review Focus

- Apparitions a 0 avec symetrie : aucune orbite de kick dans la file (pas seulement les germes de base) -> test Task 2.
- Bourgeonnement et grille en boucle : un germe pres du bord de la grille touche une tache de l'autre cote -> test Task 2 (sim).
- Apparitions qui changent pendant que des germes attendent (motif fige, tempo 0) : les germes deja en file restent, aucun crash -> test Task 2.
- Vieille scene sans les cles : defauts, image identique -> test Task 3 (`coerce_params`) + Task 2 (rendu identique).
- Curseur automatise / valeur hors plage (1.4, -1, NaN) : bornee par la table, `kick_spawns` robuste -> test Task 1.

---

### Task 1: decision pure par kick

**Files:** Modify `core/reaction.py` ; Test `tools/check_gl.py` (`check_reaction_pure`).

**Interfaces — Produces:**
- `BUD_SEEDS = 4`, `BUD_LEVEL = 0.1`
- `kick_spawns(kick_index: int, rate: float) -> bool` : `rate` borne 0..1 (NaN -> 1) ; 1 = toujours, 0 = jamais ; tirage deterministe par kick (`random.Random(kick_index * 104729 + 3)`).
- `kick_candidates(bud: bool) -> int` : `BUD_SEEDS` si bud sinon `KICK_SEEDS`.

- [ ] Test : `all(kick_spawns(k, 1.0) for k in range(200))`, `not any(kick_spawns(k, 0.0) ...)`, `80 <= sum(kick_spawns(k, 0.5) for k in range(200)) <= 120`,
  meme resultat deux fois, `kick_spawns(5, 1.4)` vrai, `kick_spawns(5, -1)` faux, `kick_spawns(5, float("nan"))` vrai ; `kick_candidates(False) == 3`, `kick_candidates(True) == 4`.
- [ ] `python tools/check_gl.py --seulement reaction_pure` : ECHEC (nom absent).
- [ ] Implementer ; relancer : vert.

### Task 2: moteur et shader

**Files:** Modify `engine/render.py` (`_labyrinth`), `engine/reaction.py` (`step(..., bud: bool = False)`), `gl_shaders/rd_step.frag` (`u_bud`,
`u_bud_level`) ; Test `check_reaction_sim`, `check_bg_labyrinthe`.

**Interfaces — Consumes:** Task 1. **Produces:** `ReactionDiffusion.step(n, shape, seeds=(), zone=None, bud=False) -> list`.

- Shader : si `u_bud > 0`, pour chaque germe, 8 lectures de V a `u_seeds[i].xy` + rayon `2 x rad + 2` cellules (angles k x 45 degres, en boucle,
  origine haut gauche comme `p`) ; le germe n'est pose que si l'une depasse `u_bud_level`.
- `_labyrinth` : pour chaque kick k, seulement si `kick_spawns(k, p["bg_rd_spawn"])` ; `kick_seeds(k, kick_candidates(bud))`.

- [ ] Tests sim : (a) grille vide + un germe, `bud=True` -> V reste 0 ; (b) une tache semee (reset) + un germe a 2 rayons de son bord,
  `bud=True` -> V > 0,2 autour du germe ; (c) germe pres du bord de la grille, tache de l'autre cote (boucle) -> pose ; (d) `bud=False` -> etat
  identique bit pour bit a l'appel sans l'argument.
- [ ] Tests rendu : `bg_rd_spawn=0` avec kicks (et symetrie quad) -> `r._rd_pending == []` ; `bg_rd_spawn=1` -> file non vide ; defauts -> image
  identique bit pour bit a l'image sans les cles ; changer `bg_rd_spawn` de 1 a 0 avec des germes en file (tempo 0) -> pas d'exception, file gardee.
- [ ] Lancer `--seulement reaction_sim` et `--seulement bg_labyrinthe` : ECHEC ; implementer ; vert.

### Task 3: reglages, GUI, Randomizer, docs

**Files:** Modify `core/params_schema.py`, `gui/gui_fond.py`, `core/randomizer.py`, `docs/fonctions/fond.md` ; Test `check_reaction_pure`, `check_randomizer`, `check_gui`.

- Table : `_p("bg_rd_spawn", 1.0, 0.0, 1.0, scope="bg", doc=...)`, `_p("bg_rd_bud", 0.0, 0.0, 1.0, scope="bg", doc=...)` apres `bg_rd_void`.
- GUI : `param_slider(rz, "Apparitions", "bg_rd_spawn", 0.0, 1.0, 0.05, ...)` et une case « Seulement contre le motif » (comme `bg_sto_invert`) apres « Vide ».
- Randomizer : en fin de tirage labyrinthe (apres `bg_rd_ground`, pour ne pas decaler les tirages existants) `bg_rd_spawn` = `round(uniform(0.4, 1.0), 2)`, `bg_rd_bud` = `float(random() < 1/3)`.
- [ ] Tests : `coerce_params({})` -> defauts 1.0 / 0.0 ; `coerce_params({"bg_rd_spawn": 3})` -> 1.0 ; scope `bg` ; Randomizer sur la palette labyrinthe tire les deux cles dans leurs plages ;
  `check_gui` : le bloc labyrinthe a « Apparitions » et la case.
- [ ] Fiche `fond.md` : paragraphe « Apparitions » (#187). Lancer `--pur` puis la suite complete.
