# Commercialisation : cadrage macro (issue #112)

Statut : brouillon. Le modele commercial n'est pas decide (voir "Decision en attente") : on garde donc
TOUS les chantiers, sans les ordonner. A reprendre quand le choix est fait.

## Point de depart (issue #112)

Deux axes si le projet est vendu : la **distribution** et l'**hyperpersonnalisation**. A developper :
**support des langues** et **wizard d'installation**. Cadrage repousse "a plus tard" : c'est ce document.

## Contraintes qui viennent du depot

- Version portable ~520 Mo dont 424 Mo de ffmpeg "full" (licence GPL du build), sans installeur ni signature
  de code (SmartScreen avertira).
- audio2wave est une copie interne (`sources/audio2wave/`) : sa licence doit permettre l'usage commercial.
- Depend du materiel du client (OpenGL, micro DirectShow / WASAPI, projecteur) ; diagnostic de demarrage
  (`--diagnostic`) et journal de session existent deja.
- Interface, documentation et messages en francais sans accents.
- Reglages et donnees dans `~/.audio2wave` (pas dans le dossier du programme).

## Chantiers

1. **Modele commercial** : cible (VJ, clubs, evenementiel), prix, licence ou abonnement, version d'essai.
2. **Juridique** : licences tierces (audio2wave, ffmpeg GPL, dependances Python, polices et medias fournis),
   CGU, vie privee du site des doodles (photos du public), marque "Casual Ravers".
3. **Distribution** *(issue)* : installeur, wizard d'installation, signature de code, mises a jour
   automatiques, ffmpeg allege ou installe a part.
4. **Licence et activation** : cle, periode d'essai, controle hors-ligne (clubs sans Wi-Fi).
5. **Hyperpersonnalisation** *(issue)* : theme et logo par client, packs de scenes, reglages de salle
   exportables, eventuellement marque blanche.
6. **Internationalisation** *(issue)* : extraire les textes de l'interface, un fichier de langue par langue,
   accents et polices. Aujourd'hui tout est code en francais sans accents.
7. **Qualite et support** : tests sur d'autres PC, materiel minimum, documentation utilisateur, canal de
   support, rapport de bogue (journal + diagnostic).
8. **Site et mise en marche** : page de vente, videos de demo, doodles cote client.

Les chantiers 3, 5 et 6 sont ceux de l'issue ; 1, 2, 4, 7 et 8 sont ajoutes par ce cadrage.

## Decision en attente

Que veut dire "vendu" ?

- **Produit** : vendre le logiciel (licence ou abonnement) a des VJ ou des clubs. Tous les chantiers.
- **Prestation** : le logiciel reste chez Casual Ravers, on vend des soirees ou installations. Les
  chantiers 3, 4 et 6 perdent presque tout interet ; la personnalisation par client (5) devient le coeur.
- **Les deux** : prestation d'abord, produit ensuite.

## Suite prevue

1. Choisir le modele (chantier 1) : ordonne tout le reste.
2. Verifier les licences tierces (chantier 2) : peut bloquer le produit, donc a faire tot meme sans decision.
3. Une issue par chantier (label `produit`), une fois ce document valide.
