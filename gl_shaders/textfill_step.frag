#version 330

// Un pas de reaction-diffusion Gray-Scott limite a l'interieur des lettres (remplissage du texte, #199).
// Etat : r = U, g = V, b = masque (1 dans une lettre, 0 dehors ; constant, sert de mur). Les cellules hors lettre ne changent pas et
// ne diffusent rien : un voisin hors masque est remplace par la cellule elle-meme (flux nul au bord, pas de fuite hors des lettres).
// Laplacien 3 x 3 et formulation identiques a rd_step.frag (Du = 1, Dv = 0,5, dt = 1).
uniform sampler2D u_state;
uniform vec2 u_texel;          // 1 / taille de la grille
uniform vec2 u_fk;             // (F, K) du modele

in vec2 v_uv;
out vec4 f_color;

vec2 nb(vec2 off, vec2 c) {
    vec4 s = texture(u_state, v_uv + off);
    return s.b > 0.5 ? s.rg : c;
}

void main() {
    vec4 s0 = texture(u_state, v_uv);
    if (s0.b < 0.5) {
        f_color = s0;
        return;
    }
    vec2 c = s0.rg;
    vec2 lap = -c;
    lap += 0.2 * (nb(vec2(u_texel.x, 0.0), c) + nb(vec2(-u_texel.x, 0.0), c) + nb(vec2(0.0, u_texel.y), c) + nb(vec2(0.0, -u_texel.y), c));
    lap += 0.05 * (nb(u_texel, c) + nb(-u_texel, c) + nb(vec2(u_texel.x, -u_texel.y), c) + nb(vec2(-u_texel.x, u_texel.y), c));
    float u = c.r;
    float v = c.g;
    float uvv = u * v * v;
    u += lap.r - uvv + u_fk.x * (1.0 - u);
    v += 0.5 * lap.g + uvv - (u_fk.x + u_fk.y) * v;
    f_color = vec4(clamp(u, 0.0, 1.0), clamp(v, 0.0, 1.0), 1.0, 1.0);
}
