#version 330

// Un pas de reaction-diffusion Gray-Scott (palette « labyrinthe » du fond genere). Etat : r = U, g = V, grille en boucle.
// Laplacien 3 x 3 (centre -1, voisins directs 0,2, diagonales 0,05), Du = 1, Dv = 0,5, dt = 1 (formulation de Karl Sims).
uniform sampler2D u_state;
uniform vec2 u_texel;          // 1 / taille de la grille
uniform vec2 u_grid;           // taille de la grille, en cellules
uniform vec2 u_fk;             // (F, K) du modele
uniform vec3 u_seeds[4];       // germes : position normalisee (origine HAUT gauche), rayon en cellules
uniform int u_nseeds;
// Zones (formes, vides) : u_zoned = (F, K) lu dans la carte calculee une fois par image (rd_zone.frag) ; sinon u_fk partout (origine).
uniform int u_zoned;
uniform sampler2D u_zone;      // carte (F, K) par cellule
uniform sampler2D u_ds;        // facteur de diffusion par cellule (densite variable, #160)
uniform int u_ds_on;           // 1 = la densite est active : u_ds est lue ; 0 = diffusion inchangee (image d'origine, aucune lecture de plus)
uniform int u_sym;             // 0 aucune, 1 miroir gauche-droite, 2 miroir haut-bas, 3 quatre quadrants, 4 rotation
uniform float u_free;          // liberte 0..1
uniform float u_free_noise;    // amplitude du bruit de V a liberte 1 (0 si l'etat est en 16 bits : il serait perdu)
uniform int u_step;            // compteur de pas : le bruit de V change a chaque pas
uniform int u_w;               // 1 = l'etat stocke W = 1 - U (demi-precision RG16F, issue #225) ; 0 = U (RG32F : chemin d'origine, bit pour bit)
uniform int u_bud;             // 1 = un germe ne prend que s'il touche de la matiere (apparitions « contre le motif », issue #187)
uniform float u_bud_level;     // V au-dessus duquel une lecture compte comme matiere (core.reaction.BUD_LEVEL)

bool touches_matter(vec2 seed, float rad) {                 // 8 lectures sur un cercle de 2 x rayon + 2 cellules autour du germe (meme decision pour tout le germe)
    vec2 c = vec2(seed.x, 1.0 - seed.y);                    // germe : origine HAUT gauche ; texture : origine bas gauche (grille en boucle : REPEAT)
    float reach = 2.0 * rad + 2.0;
    for (int k = 0; k < 8; k++) {
        float a = 0.7853982 * float(k);
        if (texture(u_state, c + vec2(cos(a), sin(a)) * reach * u_texel).g > u_bud_level) {
            return true;
        }
    }
    return false;
}

in vec2 v_uv;
out vec4 f_color;

float zhash(vec2 c) {                                       // hachage sans sin
    vec3 p3 = fract(vec3(c.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    vec2 fk = u_zoned > 0 ? texture(u_zone, v_uv).rg : u_fk;
    float ds = u_ds_on > 0 ? texture(u_ds, v_uv).r : 1.0;                      // facteur de diffusion de la cellule (#160) : lu seulement avec une densite
    vec2 c = texture(u_state, v_uv).rg;
    vec2 lap = -c;
    lap += 0.2 * (texture(u_state, v_uv + vec2(u_texel.x, 0.0)).rg + texture(u_state, v_uv - vec2(u_texel.x, 0.0)).rg
                + texture(u_state, v_uv + vec2(0.0, u_texel.y)).rg + texture(u_state, v_uv - vec2(0.0, u_texel.y)).rg);
    lap += 0.05 * (texture(u_state, v_uv + u_texel).rg + texture(u_state, v_uv - u_texel).rg
                 + texture(u_state, v_uv + vec2(u_texel.x, -u_texel.y)).rg + texture(u_state, v_uv + vec2(-u_texel.x, u_texel.y)).rg);
    lap *= ds;                                          // densite variable (#160) : diffusion x ds <= 1 (exactement 1 sans densite : image d'origine)
    float u = c.r;
    float v = c.g;
    if (u_w > 0) {
        // `u` est ici W = 1 - U : W' = W + lap(W) + U V^2 - F W (meme pas, ecrit en W ; en demi-precision l'increment F x W reste representable
        // la ou le motif est vide, alors que F x (1 - U) pres de U = 1 y etait perdu et figeait le motif)
        float uvv = (1.0 - u) * v * v;
        u += lap.r + uvv - fk.x * u;
        v += 0.5 * lap.g + uvv - (fk.x + fk.y) * v;
    } else {
        float uvv = u * v * v;
        u += 1.0 * lap.r - uvv + fk.x * (1.0 - u);
        v += 0.5 * lap.g + uvv - (fk.x + fk.y) * v;
    }
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
        float r = length(d);
        float rad = u_seeds[i].z;
        if (rad > 0.0 && r <= 2.0 * rad && (u_bud == 0 || touches_matter(u_seeds[i].xy, rad))) {      // germe doux (issue #189) : gaussien, 0,4 au centre, moitie au rayon (core.reaction.seed_value)
            v = max(v, 0.4 * exp(-0.6931472 * (r / rad) * (r / rad)));
        }
    }
    u = clamp(u, 0.0, 1.0);
    v = clamp(v, 0.0, 1.0);
    if (u_w > 0) {
        // Arrondi stochastique : un increment plus petit que l'ulp du demi-flottant (V varie de ~1e-4 par pas, l'ulp vaut 2,4e-4 vers 0,3)
        // serait arrondi a zero et le motif se figerait ; un bruit de +-1/2 ulp avant l'ecriture le conserve en moyenne.
        float ulp_u = exp2(floor(log2(max(u, 6.2e-5))) - 10.0);
        float ulp_v = exp2(floor(log2(max(v, 6.2e-5))) - 10.0);
        vec2 hp = gl_FragCoord.xy + vec2(7.0, 13.0) * float(u_step % 4096);
        u += (zhash(hp) - 0.5) * ulp_u;
        v += (zhash(hp + vec2(101.0, 53.0)) - 0.5) * ulp_v;
    }
    f_color = vec4(u, v, 0.0, 1.0);
}
