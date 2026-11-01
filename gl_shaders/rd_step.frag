#version 330

// Un pas de reaction-diffusion Gray-Scott (palette « labyrinthe » du fond genere). Etat : r = U, g = V, grille en boucle.
// Laplacien 3 x 3 (centre -1, voisins directs 0,2, diagonales 0,05), Du = 1, Dv = 0,5, dt = 1 (formulation de Karl Sims).
uniform sampler2D u_state;
uniform vec2 u_texel;          // 1 / taille de la grille
uniform vec2 u_grid;           // taille de la grille, en cellules
uniform vec2 u_fk;             // (F, K) du modele
uniform vec3 u_seeds[4];       // germes : position normalisee (origine HAUT gauche), rayon en cellules
uniform int u_nseeds;
// Zones de formes : (F, K) par texel le long du chemin points (0) -> lignes (1) -> taches (2). u_mix = 0 : u_fk partout (chemin d'origine).
uniform float u_mix;           // variete des zones
uniform float u_base;          // forme de base (0..2)
uniform float u_zone_t;        // temps de derive integre
uniform vec2 u_fk3[3];         // (F, K) de SPOTS, MAZE, BLOB
uniform vec2 u_zone_cells;     // reseau periodique du bruit des zones (cellules sur la grille)

in vec2 v_uv;
out vec4 f_color;

float zhash(vec2 c) {                                       // hachage sans sin
    vec3 p3 = fract(vec3(c.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float pnoise(vec2 q, vec2 cells) {                          // bruit de valeur periodique (indices modulo cells : la grille boucle)
    vec2 i = floor(q);
    vec2 f = fract(q);
    f = f * f * (3.0 - 2.0 * f);
    vec2 i0 = mod(i, cells);
    vec2 i1 = mod(i + 1.0, cells);
    return mix(mix(zhash(i0), zhash(vec2(i1.x, i0.y)), f.x), mix(zhash(vec2(i0.x, i1.y)), zhash(i1), f.x), f.y);
}

float zone_noise(vec2 uv) {                                  // 0..1, deux octaves, derive dans le temps
    vec2 q = uv * u_zone_cells + vec2(u_zone_t, 0.37 * u_zone_t);
    return 0.65 * pnoise(q, u_zone_cells) + 0.35 * pnoise(q * 2.0 + 5.2, u_zone_cells * 2.0);
}

vec2 zone_fk() {
    if (u_mix <= 0.0) {
        return u_fk;
    }
    float s = clamp(u_base + u_mix * (2.0 * zone_noise(v_uv) - 1.0), 0.0, 2.0);
    return s <= 1.0 ? mix(u_fk3[0], u_fk3[1], s) : mix(u_fk3[1], u_fk3[2], s - 1.0);
}

void main() {
    vec2 fk = zone_fk();
    vec2 c = texture(u_state, v_uv).rg;
    vec2 lap = -c;
    lap += 0.2 * (texture(u_state, v_uv + vec2(u_texel.x, 0.0)).rg + texture(u_state, v_uv - vec2(u_texel.x, 0.0)).rg
                + texture(u_state, v_uv + vec2(0.0, u_texel.y)).rg + texture(u_state, v_uv - vec2(0.0, u_texel.y)).rg);
    lap += 0.05 * (texture(u_state, v_uv + u_texel).rg + texture(u_state, v_uv - u_texel).rg
                 + texture(u_state, v_uv + vec2(u_texel.x, -u_texel.y)).rg + texture(u_state, v_uv + vec2(-u_texel.x, u_texel.y)).rg);
    float u = c.r;
    float v = c.g;
    float uvv = u * v * v;
    u += 1.0 * lap.r - uvv + fk.x * (1.0 - u);
    v += 0.5 * lap.g + uvv - (fk.x + fk.y) * v;
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
