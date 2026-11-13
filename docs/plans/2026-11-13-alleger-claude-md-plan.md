# Alleger CLAUDE.md : plan d'implementation

> **Pour l'executant** : suivre tache par tache (skill `superpowers:executing-plans`). Les cases `- [ ]` suivent l'avancement. Tout texte ecrit en francais
> SANS accents. Une branche neuve depuis `origin/main`, jamais de commit sur `main`.

**But** : `CLAUDE.md` racine de 300 lignes au plus, detail deplace tel quel dans 11 fiches `docs/fonctions/*.md`, pointeurs `CLAUDE.md` par dossier,
verification `check_claude_md` en CI.

**Architecture** : documentation seulement (aucun code applicatif). Une verification pure dans `tools/check_gl.py` ; la migration est un deplacement de
texte controle par un script ponctuel (jete apres usage).

**Spec** : `docs/plans/2026-11-13-alleger-claude-md-design.md` (issue #132).

## Contraintes globales

- Contenu DEPLACE TEL QUEL, sans reecriture ; lignes nouvelles seulement : index (une ligne par fonctionnalite), regle de maintenance, en-tete des fiches, pointeurs de dossier.
- Racine : au plus 300 lignes (`MAX_ROOT_LINES = 300`).
- Fiches exactement : `fond`, `logo`, `audio`, `synchro`, `strobe-foudre`, `scenes-vj`, `gui`, `projection`, `exploitation`, `medias-externes`, `outillage` (`docs/fonctions/<nom>.md`).
- Pointeurs : `core/CLAUDE.md`, `engine/CLAUDE.md`, `gui/CLAUDE.md`, `tools/CLAUDE.md` ; `sources/audio2wave/CLAUDE.md` intact.
- Francais sans accents ; liens relatifs corrects depuis l'emplacement du fichier.

## Points de vigilance (non couverts par les tests de chaque tache)

1. Une section coupee au milieu d'une liste ou d'un bloc de code (le bloc « Structure du depot » est entre ``` : il doit rester entier).
2. Liens relatifs casses apres deplacement (`[gl_gui.py](gui/gl_gui.py)` depuis `docs/fonctions/` -> `../../gui/gl_gui.py`).
3. Un `CLAUDE.md` de dossier embarque par erreur dans le portable ou lu par un test (`check_core_purity` ne lit que les `.py` : a verifier).
4. Une ligne perdue ou dupliquee lors du deplacement (controle de non-perte, tache 2).
5. Un piege transverse laisse seulement dans une fiche (les pieges listes par la spec doivent rester a la racine).

---

### Tache 1 : verification `check_claude_md` (test d'abord)

**Fichiers** : `tools/check_gl.py` (nouvelle fonction, ajoutee a `PURES` et a `SUITE`).

**Interfaces** : produit `check_claude_md() -> None` ; lit `CLAUDE.md`, `docs/fonctions/*.md`, `{core,engine,gui,tools}/CLAUDE.md`. Une fiche est « citee » si le
texte de la racine contient `docs/fonctions/<nom>.md`. Un pointeur de dossier cite une fiche par `docs/fonctions/<nom>.md` (chemin relatif quelconque : on
lit le nom de fichier).

- [ ] **Etape 1** : ecrire `check_claude_md` avec ces verifications :
  - `len(CLAUDE.md.splitlines()) <= 300` ;
  - les 11 fiches attendues existent ;
  - chaque fiche citee par la racine existe ; chaque `docs/fonctions/*.md` est citee par la racine ;
  - chaque `CLAUDE.md` de dossier existe et chaque fiche qu'il cite existe.
  (Pas de controle des accents : le texte est deplace tel quel et l'ancien en contient quelques-uns.)
- [ ] **Etape 2** : `python tools/check_gl.py --seulement check_claude_md` -> ECHEC attendu (racine de 1392 lignes, fiches absentes).
- [ ] **Etape 3** : commit « Tests : check_claude_md (racine <= 300 lignes, index des fiches) (Refs #132) ».

### Tache 2 : migration

**Fichiers** : `CLAUDE.md` (reecrit), `docs/fonctions/*.md` (11, nouveaux), `core/CLAUDE.md`, `engine/CLAUDE.md`, `gui/CLAUDE.md`, `tools/CLAUDE.md` (nouveaux).

- [ ] **Etape 1** : copier l'ancien `CLAUDE.md` dans le dossier de travail temporaire (reference du controle de non-perte).
- [ ] **Etape 2** : creer les 11 fiches en y deplacant les sections selon le tableau de la spec ; en-tete de chaque fiche : `# <Theme>` + une ligne « Fichiers :
  ... » ; corriger les liens relatifs (`../../`).
- [ ] **Etape 3** : reecrire la racine : sections gardees par la spec (contexte, audio2wave interne, conventions, structure + regle de dependance, commandes,
  architecture en bref, pieges transverses listes par la spec, verifications / CI en resume, skills / Git / board) + **index** (une ligne par fonctionnalite,
  lien vers la fiche) + **regle de maintenance**.
- [ ] **Etape 4** : pointeurs de dossier (« avant de modifier `x.py`, lis `docs/fonctions/<theme>.md` », un module ou groupe par ligne).
- [ ] **Etape 5** : controle de non-perte (script ponctuel hors depot) : chaque ligne non vide de l'ancien `CLAUDE.md` doit se retrouver identique (apres
  correction des liens) dans la racine ou une fiche ; resultat attendu : 0 ligne perdue. Noter les lignes volontairement modifiees (liens) dans la PR.
- [ ] **Etape 6** : `python tools/check_gl.py --seulement check_claude_md` -> OK ; `--pur` -> seuls les echecs locaux connus.
- [ ] **Etape 7** : commit « CLAUDE.md allege : detail par theme dans docs/fonctions, pointeurs par dossier (Refs #132) ».

### Tache 3 : verification et PR

- [ ] **Etape 1** : `check_portable_modules` et la fabrication du portable n'embarquent pas les `CLAUDE.md` de dossier (ou sans effet) ; `check_core_purity` OK.
- [ ] **Etape 2** : PR (nombre de lignes avant / apres, taille, resultat du controle de non-perte), `Closes #132` ; fusion sur CI verte.
