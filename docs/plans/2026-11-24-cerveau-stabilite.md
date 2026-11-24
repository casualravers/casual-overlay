# Cerveau : stabilite et fluidite sur de la vraie musique (spec, Refs #192)

Retour de l'utilisateur (2026-11-24) : ecart entre la musique de test et la vraie musique ; l'interface clignote et saute ; pas de
fluidite ni de previsibilite ; tout parait chaotique. Points 1 a 4 de la proposition valides par l'utilisateur (« Fais de 1 a 4 »).

1. **Mesurer d'abord** : rejouer de vrais morceaux dans la vraie analyse (et des journaux enregistres en soiree, bouton « log ») et
   compter par minute ce qui fait sauter l'affichage (pertes de grille, recalages, teintes, sauts du curseur, pages, surprises,
   manques). Outil : `tools/rejoue_cerveau.py`.
2. **Stabiliser l'affichage** sans toucher a l'analyse : grille gardee quelques secondes a sa perte, periode lissee et phase rattrapee
   sans saut, teinte de phase retenue.
3. **Fluidite** : le curseur seul avance entre deux rafraichissements (iso, mandala, sismo), dans le budget.
4. **Calmer les predictions** : rien de predit sur une grille qui vient d'arriver, surprise / manque seulement quand c'est net,
   frappes tres faibles ignorees.

Hors champ : la detection elle-meme (kick, caisse, hi-hat), les recalages du Temps 1 auto, le defilement continu des pages (point 5,
optionnel, non demande).

Detail, valeurs et mesures avant / apres : `docs/fonctions/cerveau.md`, section « Stabilite sur de la vraie musique ».
