#version 330

// Halo holographique (#240) : jump flooding a BASSE resolution (1/4 de l'ecran). Chaque texel apprend la position (uv) de la graine la plus
// proche, une graine etant un texel ou la couche logo n'est pas vide. Etat RG : la position de la graine, (-1, -1) si aucune connue.
//   u_init = 1 : graines (alpha de la couche logo moyenne sur 4 x 4 pixels, niveau 2 des mipmaps : un trait fin de 1 px en fait une).
//   u_init = 0 : une passe de pas `u_step` texels (9 voisins a +- u_step, on garde le plus proche, distance isotrope : aspect de l'ecran).
// `holo.frag` en tire la distance au contour et la direction vers lui (core/holo.py : jfa_steps, SEED_ALPHA).

uniform sampler2D u_logo;       // couche logo (rgba), mipmaps construites
uniform sampler2D u_state;      // etat de la passe precedente (RG : position de la graine)
uniform int   u_init;
uniform vec2  u_texel;          // 1 / taille de cette passe
uniform float u_step;           // pas, en texels
uniform float u_aspect;         // largeur / hauteur de l'ecran
uniform float u_seed_alpha;

in vec2 v_uv;
out vec4 f_color;

void main() {
    if (u_init == 1) {
        float a = textureLod(u_logo, v_uv, 2.0).a;
        f_color = a > u_seed_alpha ? vec4(v_uv, 0.0, 1.0) : vec4(-1.0, -1.0, 0.0, 1.0);
        return;
    }
    vec2 best = vec2(-1.0);
    float bd = 1e9;
    for (int j = -1; j <= 1; j++) {
        for (int i = -1; i <= 1; i++) {
            vec2 q = v_uv + vec2(float(i), float(j)) * u_step * u_texel;
            if (q.x < 0.0 || q.x > 1.0 || q.y < 0.0 || q.y > 1.0) {
                continue;
            }
            vec2 s = texture(u_state, q).xy;
            if (s.x < 0.0) {
                continue;
            }
            vec2 dv = (s - v_uv) * vec2(u_aspect, 1.0);
            float d = dot(dv, dv);
            if (d < bd) {
                bd = d;
                best = s;
            }
        }
    }
    f_color = vec4(best, 0.0, 1.0);
}
