#version 330

// Un pas de reaction-diffusion Gray-Scott (palette « labyrinthe » du fond genere). Etat : r = U, g = V, grille en boucle.
// Laplacien 3 x 3 (centre -1, voisins directs 0,2, diagonales 0,05), Du = 1, Dv = 0,5, dt = 1 (formulation de Karl Sims).
uniform sampler2D u_state;
uniform vec2 u_texel;          // 1 / taille de la grille
uniform vec2 u_grid;           // taille de la grille, en cellules
uniform vec2 u_fk;             // (F, K) du modele
uniform vec3 u_seeds[4];       // germes : position normalisee (origine HAUT gauche), rayon en cellules
uniform int u_nseeds;

in vec2 v_uv;
out vec4 f_color;

void main() {
    vec2 c = texture(u_state, v_uv).rg;
    vec2 lap = -c;
    lap += 0.2 * (texture(u_state, v_uv + vec2(u_texel.x, 0.0)).rg + texture(u_state, v_uv - vec2(u_texel.x, 0.0)).rg
                + texture(u_state, v_uv + vec2(0.0, u_texel.y)).rg + texture(u_state, v_uv - vec2(0.0, u_texel.y)).rg);
    lap += 0.05 * (texture(u_state, v_uv + u_texel).rg + texture(u_state, v_uv - u_texel).rg
                 + texture(u_state, v_uv + vec2(u_texel.x, -u_texel.y)).rg + texture(u_state, v_uv + vec2(-u_texel.x, u_texel.y)).rg);
    float u = c.r;
    float v = c.g;
    float uvv = u * v * v;
    u += 1.0 * lap.r - uvv + u_fk.x * (1.0 - u);
    v += 0.5 * lap.g + uvv - (u_fk.x + u_fk.y) * v;
    vec2 p = vec2(v_uv.x, 1.0 - v_uv.y);
    for (int i = 0; i < 4; i++) {
        if (i >= u_nseeds) {
            break;
        }
        vec2 d = p - u_seeds[i].xy;
        d = (d - floor(d + 0.5)) * u_grid;                  // ecart le plus court sur la grille en boucle, en cellules
        if (length(d) <= u_seeds[i].z) {
            v = 1.0;
        }
    }
    f_color = vec4(clamp(u, 0.0, 1.0), clamp(v, 0.0, 1.0), 0.0, 1.0);
}
