#version 330

// Un pas de reaction-diffusion Gray-Scott (palette « labyrinthe » du fond genere). Etat : r = U, g = V, grille en boucle.
// Laplacien 3 x 3 (centre -1, voisins directs 0,2, diagonales 0,05), Du = 1, Dv = 0,5, dt = 1 (formulation de Karl Sims).
uniform sampler2D u_state;
uniform vec2 u_texel;          // 1 / taille de la grille
uniform vec2 u_grid;           // taille de la grille, en cellules
uniform vec2 u_fk;             // (F, K) du modele
uniform vec3 u_seeds[4];       // germes : position normalisee (origine HAUT gauche), rayon en cellules
uniform int u_nseeds;
// Zones de formes : u_mix > 0 = (F, K) lu dans la carte calculee une fois par image (rd_zone.frag) ; u_mix = 0 = u_fk partout (origine).
uniform float u_mix;           // variete des zones
uniform sampler2D u_zone;      // carte (F, K) par cellule
uniform int u_sym;             // 0 aucune, 1 miroir gauche-droite, 2 miroir haut-bas, 3 quatre quadrants, 4 rotation
uniform float u_free;          // liberte 0..1
uniform float u_free_noise;    // amplitude du bruit de V a liberte 1 (0 si l'etat est en 16 bits : il serait perdu)
uniform int u_step;            // compteur de pas : le bruit de V change a chaque pas

in vec2 v_uv;
out vec4 f_color;

float zhash(vec2 c) {                                       // hachage sans sin
    vec3 p3 = fract(vec3(c.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

vec2 zone_fk() {
    return u_mix > 0.0 ? texture(u_zone, v_uv).rg : u_fk;
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
    if (u_sym > 0 && u_free > 0.0) {                          // sans lui, un etat symetrique le resterait : motif strictement en miroir
        v += u_free * u_free_noise * (zhash(gl_FragCoord.xy + vec2(17.0, 31.0) * float(u_step % 4096)) - 0.5);
    }
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
