# Alleger CLAUDE.md : detail par fonctionnalite vers docs/ (issue #132)

## Pourquoi

`CLAUDE.md` fait 1392 lignes (~250 Ko, environ 60 000 tokens) et il est charge au debut de CHAQUE session et apres chaque compaction du contexte :
demarrages plus lents, cout fixe eleve, et un risque de lecture approximative d'un texte aussi long. L'essentiel est du detail par fonctionnalite qui ne
sert qu'aux taches qui touchent cette fonctionnalite.

Objectif : des sessions qui demarrent plus vite et coutent moins, **sans perdre les pieges appris**. Risque a tenir sous controle : une session qui ne lit
pas le detail dont elle a besoin et refait une erreur deja corrigee.

## Decisions (validees avec l'utilisateur)

1. **Ce qui reste charge a chaque session** : regles de travail, Git, conventions, commandes, structure + pieges transverses + **un resume d'une ligne par
   fonctionnalite** (ce qu'elle fait, ses fichiers, son reglage) avec le lien vers sa fiche.
2. **Approche mixte** : `CLAUDE.md` racine court + fiches par theme dans `docs/` + petits `CLAUDE.md` de dossier qui ne sont que des pointeurs.
3. **Une fiche par THEME** (11 fiches), pas une par section.
4. **Garde-fou** : une regle ecrite ET une verification automatique ; racine **au plus 300 lignes**.

## Structure cible

### `CLAUDE.md` racine (<= 300 lignes)

- Contexte, audio2wave interne (copie, `VENDORED.md`, API hote et garde de version), conventions (francais sans accents), dependances, Python 3.12.
- Structure du depot (`core/` pur / `engine/` / `gui/` / `tools/` / `docs/`), **regle de dependance**, facade `audio2wave_gl`, decoupage de `build_window`
  en blocs `gui_*.py` (en bref).
- Commandes.
- Architecture : le schema du flux (micro / ffmpeg / FrameReader / analyse / rendu) et les principes (rendu a la cadence de l'ecran, latest-frame-wins,
  threads Tk / GL et `Session.commands`).
- **Pieges transverses** (ceux qui ont deja coute cher et qui touchent plusieurs themes) : pipe stdout de 4 Ko sous Windows, pas de vsync en borderless,
  `ProducerManager.args` doit etre une copie, un seul `draw` par image (integration des animations), lecture du framebuffer avant `swap_buffers`,
  `--colors grey`, uniforms elimines par le compilateur (`Renderer._set`), shader rechargeable sans ecran noir.
- Verifications et CI en resume (`check_gl.py --pur`, groupes, `FRAGILES`, `SCREEN_DEPENDENT`, code de sortie 3) ; « non verifie » pour ce qui demande le materiel.
- Workflow : skills (tableau actuel), Git (jamais sur `main`, worktrees), idees et board.
- **Index des fiches** : pour chaque theme, une ligne par fonctionnalite + le lien vers la fiche.
- **Regle de maintenance** (voir plus bas).

### `docs/fonctions/` : 11 fiches

| Fiche | Contenu deplace (sections actuelles de CLAUDE.md) |
|---|---|
| `fond.md` | Fond video, Fond genere (palettes, stochastique, labyrinthe et ses zones / symetries / epaisseur / vide / taille), reprise des medias |
| `logo.md` | Texte a incruster, textes en rotation, planning du texte, logo anime, halo holographique, fonte acide, cellules, mosaique de doodles, effets par couche, noise, automations |
| `audio.md` | Audio (FeatureExtractor, kick, telephone, kick ou caisse claire), rythme detecte, grille du temps, phases du morceau, sources audio par couche, reactivite par effet |
| `synchro.md` | Synchro son / image lots 1 a 6, avance automatique, latence mesuree, mesures de latence |
| `strobe-foudre.md` | Fonctions intelligentes (qui fait quoi), strobe auto, limiteur, montee / drop, foudre |
| `scenes-vj.md` | Scenes, sets, mode VJ, transitions, espace de controle global (onglets, masters, memoires, historique), panneau etendu (poignee, bibliotheque, lignes cochees, vignettes, glisser-deposer), Full Randomizer, securite GPU du VJ, table unique des reglages |
| `gui.md` | Design de la fenetre de reglages (theme, skin, icones, ascenseurs, menus), GUI (`--gui` : integration d'audio2wave, mise en page adaptative, presets, modes Snap / Ridge / Sphere, gates, couleurs, ecran de chargement, fluidite) |
| `projection.md` | Fenetre GL, plein ecran et moniteurs, projection a la demande, reprise automatique, charge GPU (garde-fou, optimisations), mesures de rendu |
| `exploitation.md` | Garde-fous d'exploitation (panique, tap tempo, journal de session, auto-sauvegarde, palette Ctrl+K, mode scene, aide et console) |
| `medias-externes.md` | Doodles du site, QR code, telecommande telephone, enregistrement video + audio |
| `outillage.md` | Diagnostic de demarrage, version portable, lanceur `start.bat`, verifications detaillees, integration continue (groupes, options), mesures du poste de dev, hors POC |

Chaque fiche commence par une ligne de titre et la liste des fichiers du code qu'elle couvre (pour qu'une session la retrouve a partir d'un fichier).

### Pointeurs par dossier

`core/CLAUDE.md`, `engine/CLAUDE.md`, `gui/CLAUDE.md`, `tools/CLAUDE.md` : quelques lignes chacun, de la forme « avant de modifier `x.py`, lis
`docs/fonctions/<theme>.md` », un module ou un groupe de modules par ligne. Claude Code les charge automatiquement quand une session lit un fichier
du dossier : le pointeur arrive au bon moment, sans dupliquer le contenu. `sources/audio2wave/CLAUDE.md` (copie d'audio2wave) ne change pas.

## Migration

- Le contenu est **deplace tel quel**, section par section, **sans reecriture** (meme regle que pour le decoupage de `build_window`). Les seules lignes
  nouvelles sont : les resumes de l'index, les pointeurs de dossier, la regle de maintenance et l'en-tete de chaque fiche.
- **Controle de non-perte** (script ponctuel, jete apres usage) : chaque ligne non vide de l'ancien `CLAUDE.md` doit se retrouver, identique, soit dans
  le nouveau `CLAUDE.md`, soit dans une fiche. Le resultat (0 ligne perdue) est mis dans la PR.
- Les liens relatifs (`[gl_gui.py](gui/gl_gui.py)`...) sont corriges selon le nouvel emplacement (depuis `docs/fonctions/` : `../../gui/gl_gui.py`).
- Une seule PR pour la migration (le texte bouge d'un coup : deux etats intermediaires seraient pires qu'un seul grand deplacement).

## Garde-fou

**Regle ecrite** (dans `CLAUDE.md`) : une nouvelle fonctionnalite ou un nouveau piege va dans la fiche de son theme ; la racine ne recoit qu'une ligne
de resume dans l'index (ou un piege s'il est vraiment transverse). Un nouveau theme = une nouvelle fiche + sa ligne dans l'index.

**Verification automatique** `check_claude_md` dans `tools/check_gl.py` (dans `PURES` et `SUITE` : CI a chaque PR) :
- `CLAUDE.md` racine : au plus 300 lignes ;
- chaque fiche citee par l'index existe ;
- chaque fichier de `docs/fonctions/` est cite par l'index ;
- les `CLAUDE.md` de dossier ne pointent que vers des fiches existantes.

## Hors perimetre

- Reecrire ou resumer le contenu des fiches (il est deplace tel quel ; un nettoyage eventuel sera une autre idee).
- Les specs et plans de `docs/plans/` (inchanges).
- `sources/audio2wave/CLAUDE.md`.

## Effet attendu et verification

- Environ 60 000 tokens de moins a chaque demarrage et a chaque compaction (~250 lignes chargees au lieu de 1392).
- Verifie par : `check_claude_md`, le controle de non-perte (0 ligne perdue), `check_gl.py --pur`, et le nombre de lignes de la racine.
- **Non verifiable automatiquement** : qu'une session lise bien la fiche quand elle en a besoin (attenue par les resumes, les pointeurs de dossier et la
  regle « lire la fiche du theme avant d'y toucher »).
