#version 330

// Passe "champ holographique", a BASSE resolution (1/4 de l'ecran) : transforme la distance au contour du sujet (jump flooding, holo_jfa.frag)
// en champ lu par le post-traitement. Pour chaque texel elle ecrit
//     r = v  : 1 contre le sujet, 0 a la portee (core.holo.halo_v)
//     g, b   : direction (unitaire, espace uv) qui pointe vers le sujet
//     a      : densite locale du logo (0 hors du logo, 1 en plein dedans)
// Les anneaux sont des COPIES DU CONTOUR (distance exacte) pres du sujet et s'arrondissent doucement vers un cercle loin de lui
// (core.holo.ring_distance) : le halo garde la forme du sujet (texte, video, logo, plusieurs formes) au lieu d'un ovale centre.
// Memes formules que core/holo.py (ROUND_START / ROUND_END / CIRC_K / CURVE).

uniform sampler2D u_logo;       // couche logo (rgba), mipmaps construites (densite locale `own`)
uniform sampler2D u_state;      // jump flooding termine : RG = position (uv) de la graine la plus proche, (-1, -1) si aucune
uniform vec2  u_res;            // taille de l'ecran en pixels (pas celle de cette passe)
uniform vec4  u_holo_shape;     // x = portee (hauteurs d'ecran), y = rayon du cercle de l'arrondi, z = 1 si l'arrondi est actif
uniform vec2  u_holo_center;    // centre du logo (uv, origine bas gauche) : centre du cercle de l'arrondi
uniform float u_holo_lod0;      // log2(hauteur de l'ecran / 720)
uniform vec2  u_holo_wobble;    // x = irregularite des anneaux (0..1 ; 0 = anneaux parfaits, comme avant), y = temps du bruit (integre cote CPU)

in vec2 v_uv;
out vec4 f_color;

const float ROUND_START = 0.2;
const float ROUND_END = 2.7;
const float CIRC_K = 0.9;
const float CURVE = 1.0;
// Irregularites des anneaux (#256) : memes valeurs que core.holo (WOBBLE_*)
const float WOBBLE_AMP = 0.11;
const float WOBBLE_FREQ = 4.0;
const float WOBBLE_RING_K = 9.0;
const float WOBBLE_FAR = 0.12;
const vec2  WOBBLE_NEAR = vec2(0.80, 0.97);

float wob_hash(vec3 p) {
    p = fract(p * 0.1031);
    p += dot(p, p.zyx + 31.32);
    return fract((p.x + p.y) * p.z);
}

float wob_noise(vec3 p) {                                   // bruit de valeur 3D, 0..1
    vec3 i = floor(p);
    vec3 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float a = mix(mix(wob_hash(i), wob_hash(i + vec3(1, 0, 0)), f.x), mix(wob_hash(i + vec3(0, 1, 0)), wob_hash(i + vec3(1, 1, 0)), f.x), f.y);
    float b = mix(mix(wob_hash(i + vec3(0, 0, 1)), wob_hash(i + vec3(1, 0, 1)), f.x), mix(wob_hash(i + vec3(0, 1, 1)), wob_hash(i + vec3(1, 1, 1)), f.x), f.y);
    return mix(a, b, f.z);
}

void main() {
    float aspect = u_res.x / u_res.y;
    vec2 s = texture(u_state, v_uv).xy;
    // Densite locale du logo (flou d'environ 10 px a 720 p) a la vraie position: sert a eteindre la lumiere et la deformation DANS le logo
    // (les trous d'un logo a traits fins, sinon, laisseraient voir le halo au travers et le rendraient illisible).
    float own = textureLod(u_logo, v_uv, max(u_holo_lod0 + 3.8, 0.0)).a;
    if (s.x < 0.0) {                                        // aucune graine (couche vide) : pas de halo
        f_color = vec4(0.0, 0.0, 0.0, own);
        return;
    }
    vec2 dv = (s - v_uv) * vec2(aspect, 1.0);               // vers le sujet, en hauteurs d'ecran
    float d = length(dv);
    float dmax = max(u_holo_shape.x, 0.001);
    float w = smoothstep(ROUND_START * dmax, ROUND_END * dmax, d) * u_holo_shape.z;
    float dcirc = max(length((v_uv - u_holo_center) * vec2(aspect, 1.0)) - u_holo_shape.y, 0.0);
    float sd = d * (1.0 - w) + CIRC_K * w * dcirc;
    float v = pow(1.0 - clamp(sd / dmax, 0.0, 1.0), CURVE);
    if (u_holo_wobble.x > 0.0) {
        // Irregularites (#256) : v sur le 3e axe du bruit = chaque anneau a sa propre deformation ; le temps fait vivre le tout (core.holo.wobble_v)
        float t = u_holo_wobble.y;
        float n = 2.0 * wob_noise(vec3(v_uv * vec2(aspect, 1.0) * WOBBLE_FREQ + vec2(0.7, -0.4) * t, v * WOBBLE_RING_K + 0.6 * t)) - 1.0;
        float m = smoothstep(0.0, WOBBLE_FAR, v) * (1.0 - smoothstep(WOBBLE_NEAR.x, WOBBLE_NEAR.y, v));
        v = clamp(v + min(u_holo_wobble.x, 1.0) * WOBBLE_AMP * m * n, 0.0, 1.0);
    }
    vec2 g = d > 0.0001 ? vec2(dv.x * aspect, dv.y) / d : vec2(0.0);   // direction du gradient de v dans l'espace uv (v croit vers le sujet)
    f_color = vec4(v, g, own);
}
