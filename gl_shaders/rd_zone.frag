#version 330

// Carte de forme de la palette « labyrinthe » : (F, K) par cellule de la grille, calculee UNE fois par image (rd_step.frag la lit a chaque
// pas). Chemin points (0) -> lignes (1) -> taches (2), bruit lent qui derive, replie par la symetrie de colonisation ; la Liberte y ajoute un
// bruit non replie. La recalculer a chaque pas coutait jusqu'a +11 ms par image (rotation, 24 pas, Intel UHD 605).
uniform float u_mix;           // variete des zones
uniform float u_base;          // forme de base (0..2)
uniform float u_zone_t;        // temps de derive integre
uniform vec2 u_fk3[3];         // (F, K) de SPOTS, MAZE, BLOB
uniform vec2 u_zone_cells;     // reseau periodique du bruit des zones (cellules sur la grille)
uniform int u_sym;             // 0 aucune, 1 miroir gauche-droite, 2 miroir haut-bas, 3 quatre quadrants, 4 rotation
uniform int u_branches;        // branches de la rotation
uniform float u_free;          // liberte 0..1
uniform float u_aspect;        // largeur / hauteur de la grille (rotation ronde a l'ecran)
uniform float u_free_zone;     // part du bruit non replie des zones a liberte 1

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

vec2 fold(vec2 uv) {                                         // coordonnees des zones repliees par la symetrie choisie
    if (u_sym == 1 || u_sym == 3) {
        uv.x = 0.5 - abs(uv.x - 0.5);
    }
    if (u_sym == 2 || u_sym == 3) {
        uv.y = 0.5 - abs(uv.y - 0.5);
    }
    if (u_sym == 4) {
        vec2 d = vec2((uv.x - 0.5) * u_aspect, uv.y - 0.5);  // repere isotrope centre
        float sector = 6.2831853 / float(max(u_branches, 2));
        float a = mod(atan(d.y, d.x), sector);
        a = min(a, sector - a);                               // miroir dans le secteur : pas de couture entre deux branches
        float r = length(d);
        uv = vec2(0.5 + r * cos(a) / u_aspect, 0.5 + r * sin(a));
    }
    return uv;
}

void main() {
    float z = 2.0 * zone_noise(fold(v_uv)) - 1.0;
    if (u_sym > 0) {
        z += u_free * u_free_zone * (2.0 * zone_noise(v_uv + vec2(0.31, 0.17)) - 1.0);
    }
    float s = clamp(u_base + u_mix * z, 0.0, 2.0);
    vec2 fk = s <= 1.0 ? mix(u_fk3[0], u_fk3[1], s) : mix(u_fk3[1], u_fk3[2], s - 1.0);
    f_color = vec4(fk, 0.0, 1.0);
}
