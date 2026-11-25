#version 330

// Un pas de reaction-diffusion Gray-Scott sur la tuile periodique du remplissage des lettres (#199). Etat : r = U, g = V, b = instant d'arrivee
// (pas / TILE_STEPS quand V a depasse le seuil pour la premiere fois, 0 tant que non), a = 1. La tuile est calculee une fois (puis mise en
// cache) ; `scene.frag` la rejoue ensuite avec une vague. Laplacien 3 x 3 et formulation de rd_step.frag (Du = 1, Dv = 0,5, dt = 1), les
// deux diffusions multipliees par u_diff (< 1 = motif plus petit). Lectures exactes (texelFetch), grille en boucle.
uniform sampler2D u_state;
uniform vec2 u_fk;             // (F, K) du modele
uniform float u_diff;          // facteur sur les deux diffusions
uniform float u_stepn;         // numero du pas / nombre total de pas (0..1) : sert a dater l'arrivee
uniform float u_threshold;     // V au-dela duquel une cellule est dite colonisee

out vec4 f_color;

ivec2 cell;
ivec2 size;

vec4 at(ivec2 d) {
    return texelFetch(u_state, (cell + d + size) % size, 0);
}

void main() {
    cell = ivec2(gl_FragCoord.xy);
    size = textureSize(u_state, 0);
    vec4 s0 = at(ivec2(0));
    vec2 c = s0.rg;
    vec2 lap = -c;
    lap += 0.2 * (at(ivec2(1, 0)).rg + at(ivec2(-1, 0)).rg + at(ivec2(0, 1)).rg + at(ivec2(0, -1)).rg);
    lap += 0.05 * (at(ivec2(1, 1)).rg + at(ivec2(-1, -1)).rg + at(ivec2(1, -1)).rg + at(ivec2(-1, 1)).rg);
    float u = c.r;
    float v = c.g;
    float uvv = u * v * v;
    u += u_diff * lap.r - uvv + u_fk.x * (1.0 - u);
    v += 0.5 * u_diff * lap.g + uvv - (u_fk.x + u_fk.y) * v;
    u = clamp(u, 0.0, 1.0);
    v = clamp(v, 0.0, 1.0);
    float arrival = s0.b;
    if (arrival == 0.0 && v > u_threshold) {
        arrival = u_stepn;
    }
    f_color = vec4(u, v, arrival, 1.0);
}
