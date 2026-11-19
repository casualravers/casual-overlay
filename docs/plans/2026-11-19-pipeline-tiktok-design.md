# Pipeline de publication TikTok : spec (issue #176)

Statut : spec a valider (discussion dans le chat). Le code du pipeline vivra dans un **depot separe** ; cette spec reste ici pour la suite des idees du board.

## But

Un programme **independant de casual-overlay** : il scanne un dossier cloud de videos 9:16 deja pretes (typiquement les extraits de #148), et en publie **2 par jour** sur un compte TikTok, **sans validation humaine**. Rien ne change dans casual-overlay.

## Decisions (validees avec l'utilisateur)

| Sujet | Decision |
|---|---|
| Perimetre | Scan d'un dossier, publication, rien dans casual-overlay |
| Cadence | 2 publications par jour |
| Horaires | **Fenetre aleatoire** : une heure tiree au hasard dans une plage du matin / du soir (plages et fuseau en configuration) |
| Validation | Aucune, tout automatique |
| Envoi | **API officielle TikTok Content Posting** (Direct Post), avec audit de l'application |
| Legende / hashtags | **Generes par le pipeline** (modeles de texte, hashtags generiques ; pas de fichier a fournir) |
| Ordre | **Les plus anciennes d'abord** (date de modification du fichier) |
| Transfert PC -> serveur | **Dossier cloud** (Drive, Dropbox, S3...) que le pipeline scanne |
| Hebergement | **Serveur / VPS ou GitHub Actions** (tourne sans le PC) |
| Apres publication | La video est **deplacee dans « publiees »** |
| Erreur | **E-mail** de notification |
| Code | **Nouveau depot separe** |

## Contraintes TikTok (a verifier a la source avant le code)

D'apres la documentation de TikTok for Developers et des pages tierces, a re-verifier :

- Une application **non auditee ne peut publier qu'en prive** (`SELF_ONLY`) ; une publication publique renvoie 403 (`unaudited_client_can_only_post_to_private_accounts`). Un client non audite est limite a quelques utilisateurs et son compte doit etre prive.
- **L'audit** prend de quelques jours a deux semaines. Les posts faits en prive avant l'audit ne deviennent pas publics apres coup.
- **Plafond d'environ 15 publications par jour et par compte** (varie selon les createurs ; autres sources : 15 a 25). 2 par jour est tres en dessous.
- Limite de requetes : environ **6 initialisations de publication par minute** et par jeton ; le suivi de statut a sa propre limite.
- Deux modes d'envoi : **`FILE_UPLOAD`** (le pipeline envoie le fichier) ou **`PULL_FROM_URL`** (TikTok le telecharge depuis une URL, avec verification de domaine ; non confirme). Proposition : `FILE_UPLOAD`, qui evite d'exposer le dossier.

### Risque principal : audit sans validation humaine

L'audit examine l'usage prevu et, selon les sources, une **interface de publication imposee** (le createur voit le contenu, la legende, la visibilite, et consent a chaque post). Un pipeline « sans validation, sans interface » pourrait etre **refuse**. Decision de l'utilisateur : tenter le tout automatique. **Plan B a garder sous la main** si l'audit est refuse : (a) outil tiers deja audite (Postiz auto-heberge, Ayrshare, Blotato...) ; (b) depot en brouillon puis validation d'un tap. Le code du pipeline isole donc l'envoi derriere une interface (`Publisher`) pour changer de methode sans toucher au reste.

## Architecture

Programme Python (a confirmer), une execution = un passage, lancee par un planificateur (cron du VPS ou workflow GitHub Actions).

```
Planificateur (a l'heure tiree) -> run
   1. charge l'etat (publiees, heures du jour deja tirees)
   2. scanne le dossier cloud (`Source`)
   3. choisit la plus ancienne video valide
   4. valide le fichier (9:16, duree, taille, format)
   5. genere legende + hashtags (`Caption`)
   6. publie (`Publisher`, API TikTok)
   7. deplace dans « publiees » (`Source`), met a jour l'etat
   8. erreur -> e-mail (`Notifier`), la video reste en attente
```

Unites, chacune avec une seule responsabilite :

- **Source** : liste / telecharge / deplace les fichiers du dossier cloud (une implementation par fournisseur ; interface commune).
- **Scheduler** : tire deux heures par jour dans les plages configurees ; deduit si une publication est due ; **sans etat cote planificateur** (l'etat est relu a chaque passage).
- **Validator** : refuse un fichier hors criteres (rapport, duree, taille, extension) avant tout envoi.
- **Caption** : modeles de texte et hashtags generiques, avec variation pour eviter la repetition.
- **Publisher** : `TikTokPublisher` (OAuth, renouvellement du jeton, `FILE_UPLOAD`, suivi du statut jusqu'a la fin du traitement).
- **State** : liste des videos publiees et des heures tirees ; permet l'**idempotence** (pas de double publication apres un plantage entre l'envoi et le deplacement).
- **Notifier** : e-mail en cas d'erreur.
- **Config** : dossier source, plages horaires, fuseau, modeles de legende, destinataire ; **secrets** (jetons TikTok, mot de passe SMTP) dans le gestionnaire de secrets de l'hebergement, jamais dans le depot ni les journaux.

## Cas limites

- Dossier vide : rien ne se passe, sans erreur.
- Moins de deux videos pour la journee : on publie ce qui existe, pas d'erreur.
- Plantage apres l'envoi et avant le deplacement : l'etat note l'envoi pour ne pas reposter ; reprise au passage suivant.
- Jeton expire : renouvellement ; si impossible, e-mail et arret propre.
- Plafond atteint (`spam_risk_too_many_posts`) ou 429 : on reessaie plus tard dans la fenetre, puis e-mail apres N echecs.
- Fichier invalide : deplace dans un dossier « rejetees » avec le motif, e-mail, les suivants continuent.
- Plusieurs passages en meme temps (deux declenchements) : verrou d'etat pour eviter deux publications simultanees.
- Fuseau et heure d'ete : les heures tirees sont stockees en UTC avec le fuseau de configuration.

## Hors perimetre (pour l'instant)

Autres plateformes (Reels, Shorts), validation manuelle, interface web, generation de legende par IA, modification de casual-overlay.

## Tests

Logique pure testee sans reseau : `Scheduler` (tirage, deux par jour, fuseau, heure d'ete), `Validator`, `Caption`, `State` (idempotence). `Publisher` et `Source` derriere des doubles de test ; un test de bout en bout avec un faux TikTok. Verification reelle (compte, audit, e-mail, cloud) : **non verifiee automatiquement**.

## Questions encore ouvertes

- Plages horaires exactes, fuseau, et marge entre les deux publications du jour.
- Fournisseur cloud concret (Drive, Dropbox, S3) et compte de service.
- VPS ou GitHub Actions (le tirage aleatoire des heures s'adapte mieux a un cron frequent qui decide a chaque passage).
- Compte createur ou personnel pour l'audit, et site vitrine / politique de confidentialite requis pour la demande de l'appli.
- Format d'un fichier valide (duree max, taille max, resolution minimale).

Lie : #148 (extraits), #140 (enregistrement).
