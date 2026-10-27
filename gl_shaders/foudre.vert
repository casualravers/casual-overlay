#version 330

// Foudre : un ruban par segment du reseau. Repere des sommets = centre de l'image, hauteur de l'image = 1 (y vers le haut),
// largeur = u_aspect ; sortie en coordonnees de clip. L'intensite est calculee ici : vigueur de la branche, ondes qui voyagent
// le long des routes (u_pulse : rayon en distance de chemin, energie), respiration (basses) et scintillement des fines (aigus).
uniform float u_aspect;
uniform float u_time;
uniform float u_bass;
uniform float u_high;
uniform vec2 u_pulse[8];

in vec2 in_ctr;         // extremite du segment
in vec2 in_off;         // decalage du coin par rapport a l'extremite (demi-largeur, bouts prolonges)
in float in_across;     // -1 .. 1 en travers du ruban
in float in_vig;       // vigueur 0..1 (le max de celle de la branche et de ses descendantes)
in float in_dist;       // distance de chemin depuis la racine
in float in_fine;       // 0 = tronc .. 1 = brindille
in float in_cov;        // couverture du ruban (un ruban elargi au minimum d'un texel garde de l'intensite)

out float v_across;
out float v_int;

void main() {
    v_across = in_across;
    vec2 pos = in_ctr + in_off * (1.0 + 0.3 * u_bass);
    float pulse = 0.0;
    for (int k = 0; k < 8; k++) {
        float d = (in_dist - u_pulse[k].x) / 0.07;
        pulse += u_pulse[k].y * exp(-d * d);
    }
    float alive = smoothstep(0.0, 0.08, in_vig);                           // une branche qui meurt s'eteint doucement
    float scint = 1.0 - 0.3 * in_fine * (1.0 - u_high * (0.5 + 0.5 * sin(40.0 * u_time + 13.0 * float(gl_VertexID / 6))));
    float rad = smoothstep(0.0, 0.6, length(in_ctr));                       // 0 au centre .. 1 vers la peripherie
    float calm = 1.0 - 0.6 * (1.0 - in_fine) * (1.0 - rad);                // les grosses sections s'adoucissent vers le centre
    v_int = min(1.0, (0.12 + 0.55 * in_vig + 0.8 * pulse) * alive * (1.0 + 0.25 * u_bass) * scint * in_cov);
    gl_Position = vec4(pos.x / (0.5 * u_aspect), pos.y / 0.5, 0.0, 1.0);
}
