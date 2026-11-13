# gl_shaders/ : shaders GLSL (rechargeables a chaud avec R)

Avant de modifier un shader, lis la fiche du theme touche :

- `scene.frag` : fond genere (`background_pattern`, `stochastic_pattern`, `labyrinth_pattern`) [docs/fonctions/fond.md](../docs/fonctions/fond.md) ; logo, fonte, cellules, mosaique (`logo_at`) [docs/fonctions/logo.md](../docs/fonctions/logo.md)
- `post.frag` : effets par couche, halo, composition [docs/fonctions/logo.md](../docs/fonctions/logo.md) ; strobe, foudre [docs/fonctions/strobe-foudre.md](../docs/fonctions/strobe-foudre.md) ; transitions de scene [docs/fonctions/scenes-vj.md](../docs/fonctions/scenes-vj.md)
- `holo.frag` : [docs/fonctions/logo.md](../docs/fonctions/logo.md)
- `rd_step.frag`, `rd_zone.frag` (labyrinthe) : [docs/fonctions/fond.md](../docs/fonctions/fond.md)
- `foudre.vert`, `foudre.frag` : [docs/fonctions/strobe-foudre.md](../docs/fonctions/strobe-foudre.md)
- `mode_*.vert` / `mode_*.frag` (Snap, Ridge, Sphere dessines en GL) : [docs/fonctions/gui.md](../docs/fonctions/gui.md)
