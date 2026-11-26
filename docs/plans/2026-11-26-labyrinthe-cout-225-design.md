# Labyrinthe : cout de la simulation (issue #225) - design court

Valide dans la conversation (options 1 et 2 ; l'option 3, le compute shader, est dans l'issue #234).

## Intention

Le labyrinthe coute trop cher aux tailles de motif fines (les plus belles, selon l'utilisateur : echelle 0,3 a 0,5), sur ce poste (Radeon integree).
Mesure (1280 x 720, mediane de `Renderer.draw`, par rapport au duo a 2,1 ms) : echelle 1 +1,9 ms, 0,5 +3,9 ms, 0,3 +5,7 ms. Le temps part presque
entierement dans les pas de simulation (cellules x pas) ; la carte des zones (0,1 ms), le lissage (0,1 ms) et le rendu (0,3 ms) comptent peu.
Cout d'UN pas (ms) : 384 x 216 0,100, 768 x 432 0,299, 1280 x 720 0,669 en RG32F ; 0,072 / 0,150 / 0,362 en RG16F. Plancher : ~0,05 ms par passe.

## Decisions (utilisateur)

1. **Stockage en demi-precision** (RG16F) de l'etat, sans changer le motif de nature.
2. **Vitesse rendue au motif fin** : a l'echelle 0,3 le moteur ne fait que 5 pas par image (plafond `MAX_CELL_STEPS`) : le motif y evolue deux
   fois moins vite. Avec le pas moins cher, le plafond monte : 10 pas par image (cout a peu pres inchange, motif a pleine vitesse).

## Decisions (Claude, a corriger si besoin)

- **Piege de la demi-precision** (fiche du fond) : l'increment F x (1 - U) pres de U = 1 est perdu et le motif se fige. Contournement : en demi-precision
  l'etat stocke W = 1 - U (petit la ou le motif est vide, donc precis) ; le shader fait le pas en W (`W' = W + lap(W) + U V^2 - F W`, algebre exacte),
  U = 1 - W. Chemin RG32F inchange, **bit pour bit** (`u_w` = 0).
- **Demi-precision seulement sans symetrie** (`bg_rd_sym` = none, le defaut) : avec une symetrie, le bruit de « Liberte » (1e-3 sur V) serait perdu
  en 16 bits ; l'etat reste en RG32F (cout et vitesse d'aujourd'hui). La symetrie qui change relance deja le motif : le format change a ce moment.
- Carte des zones (F, K) gardee en RG32F (precision de F et K), texture de lissage de l'affichage en demi-precision (comme l'etat).
- Plafond de pas : `MAX_CELL_STEPS_HALF` = 9 500 000 (10 pas a 1280 x 720, 24 a 0,5) pour la demi-precision ; `MAX_CELL_STEPS` (5 000 000) pour RG32F.
- Securite GPU (`step_load`) : un pas en demi-precision compte `HALF_STEP_COST` = 0,55 (mesure : 0,50 a 0,54 aux grilles fines).
- **Le motif n'est plus identique au pixel pres** (simulation chaotique : l'arrondi change tout) ; meme nature (couverture, composantes). A accepter.

## Verifications

- Pur : `steps_for` / `max_steps` / `step_load` selon le mode (5 pas a 0,3 en RG32F, 10 en demi-precision, 24 a 0,5).
- Simulation : aller-retour ecriture / lecture en demi-precision ; meme nature qu'en RG32F (couverture, composantes) sur les trois formes ; le motif
  se reorganise encore apres 10 000 pas (le piege) ; format change avec la symetrie ; RG32F bit pour bit.
- Rendu : defaut = demi-precision, symetrie = RG32F ; 10 pas par image a 0,3.
- Cout mesure avant / apres (echelles 1, 0,5, 0,3). **Non verifie** : le rendu au videoprojecteur ; la puce faible (Intel UHD).
