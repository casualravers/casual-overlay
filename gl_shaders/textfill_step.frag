#version 330

// Un pas de reaction-diffusion Gray-Scott limite a l'interieur des lettres (remplissage du texte, #199).
// Etat : r = U, g = V, b = masque (1 dans une lettre, 0 dehors ; constant, sert de mur). Les cellules hors lettre ne changent pas et
// ne diffusent rien : un voisin hors masque est remplace par la cellule elle-meme (flux nul au bord, pas de fuite hors des lettres).
// Laplacien 3 x 3 et formulation identiques a rd_step.frag (Du = 1, Dv = 0,5, dt = 1). Lectures exactes (texelFetch) : un echantillonnage
// bilineaire melangerait un peu de V dans les cellules hors des lettres (observe avec le rendu logiciel de la CI).
uniform sampler2D u_state;
uniform vec2 u_fk;             // (F, K) du modele
uniform float u_diff;          // facteur sur les deux diffusions : < 1 = motif plus petit (l'echelle du motif suit sa racine carree)

out vec4 f_color;

ivec2 cell;
ivec2 last;

vec4 at(ivec2 d) {
    return texelFetch(u_state, clamp(cell + d, ivec2(0), last), 0);
}

vec2 nb(ivec2 d, vec2 c) {
    vec4 s = at(d);
    return s.b > 0.5 ? s.rg : c;
}

void main() {
    cell = ivec2(gl_FragCoord.xy);
    last = textureSize(u_state, 0) - 1;
    vec4 s0 = at(ivec2(0));
    if (s0.b < 0.5) {
        f_color = s0;
        return;
    }
    vec2 c = s0.rg;
    vec2 lap = -c;
    lap += 0.2 * (nb(ivec2(1, 0), c) + nb(ivec2(-1, 0), c) + nb(ivec2(0, 1), c) + nb(ivec2(0, -1), c));
    lap += 0.05 * (nb(ivec2(1, 1), c) + nb(ivec2(-1, -1), c) + nb(ivec2(1, -1), c) + nb(ivec2(-1, 1), c));
    float u = c.r;
    float v = c.g;
    float uvv = u * v * v;
    u += u_diff * lap.r - uvv + u_fk.x * (1.0 - u);
    v += 0.5 * u_diff * lap.g + uvv - (u_fk.x + u_fk.y) * v;
    f_color = vec4(clamp(u, 0.0, 1.0), clamp(v, 0.0, 1.0), 1.0, 1.0);
}
