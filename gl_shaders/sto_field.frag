#version 330

// Palette stochastique (issue #229) : champ BRUT (forme seule, 0..1) calcule une fois par image dans une petite texture (quart de la scene),
// lu ensuite par stochastic_pattern() (scene.frag) au centre de chaque point. Le coup de contraste du kick et la densite (basses) sont appliques
// dans scene.frag. Champs : 0 taches (bruit de valeur, celui d'origine), 1 vagues, 2 degrade, 3 organique (Perlin), 4 cellules (Worley),
// 5 reseau (bords des cellules), 6 veines (cretes), 7 marbre (domaine deforme). Chacun ramene a 0..1, moyenne proche de 0,5.
uniform vec2 u_res;            // taille de la SCENE (ratio de l'ecran), pas celle de la texture
uniform float u_phase;         // derive (Renderer._bg_scroll)
uniform float u_angle;         // bg_angle, degres (vagues, degrade)
uniform float u_kick;          // compte des kicks (vagues : un quart d'onde par kick)
uniform int u_field;

in vec2 v_uv;
out vec4 f_color;

float sto_hash(vec2 p) {                                    // meme hachage que scene.frag
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float vnoise(vec2 p) {                                      // bruit de valeur (taches d'origine)
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(sto_hash(i), sto_hash(i + vec2(1.0, 0.0)), f.x),
               mix(sto_hash(i + vec2(0.0, 1.0)), sto_hash(i + vec2(1.0, 1.0)), f.x), f.y);
}

vec2 grad(vec2 c) {
    float a = 6.2831853 * sto_hash(c + 0.37);
    return vec2(cos(a), sin(a));
}

float gnoise(vec2 p) {                                      // bruit de gradient (Perlin), environ -0,7..0,7
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    float a = dot(grad(i), f);
    float b = dot(grad(i + vec2(1.0, 0.0)), f - vec2(1.0, 0.0));
    float c = dot(grad(i + vec2(0.0, 1.0)), f - vec2(0.0, 1.0));
    float d = dot(grad(i + vec2(1.0, 1.0)), f - vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {                                         // 4 octaves de gradient, environ -0,6..0,6
    float s = 0.0;
    float a = 0.5;
    for (int k = 0; k < 4; k++) {
        s += a * gnoise(p);
        p = p * 2.03 + vec2(17.0, 9.0);
        a *= 0.5;
    }
    return s;
}

vec2 worley(vec2 p, float ph) {                             // (F1, F2) : distance au germe le plus proche et au suivant ; germes qui orbitent
    vec2 i = floor(p);
    vec2 f = fract(p);
    float d1 = 8.0;
    float d2 = 8.0;
    for (int y = -1; y <= 1; y++) {
        for (int x = -1; x <= 1; x++) {
            vec2 o = vec2(float(x), float(y));
            vec2 c = i + o;
            float h = sto_hash(c + 5.1);
            vec2 g = 0.5 + 0.38 * vec2(cos(ph * 0.6 + 6.2831853 * h), sin(ph * 0.5 + 6.2831853 * sto_hash(c + 2.9)));
            vec2 r = o + g - f;
            float d = dot(r, r);
            if (d < d1) {
                d2 = d1;
                d1 = d;
            } else if (d < d2) {
                d2 = d;
            }
        }
    }
    return sqrt(vec2(d1, d2));
}

void main() {
    vec2 q = vec2(v_uv.x, 1.0 - v_uv.y);                    // origine haut gauche, comme scene.frag
    float ar = u_res.x / max(u_res.y, 1.0);
    vec2 v = vec2(q.x * ar, q.y);
    float ph = u_phase;
    float a = radians(u_angle);
    vec2 dir = vec2(cos(a), sin(a));
    vec2 drift = vec2(ph * 0.11, ph * 0.07);
    float f;
    if (u_field == 1) {                                     // vagues : bandes qui se propagent vers bg_angle
        float d = dot(v - vec2(ar * 0.5, 0.5), dir);
        f = 0.5 + 0.5 * sin(6.2831853 * (d * 3.0 - ph * 0.25 - u_kick * 0.25));
    } else if (u_field == 2) {                              // degrade aller-retour (sans couture)
        float t = dot(q - 0.5, dir) + 0.5 + ph * 0.1;
        f = abs(fract(t) * 2.0 - 1.0);
    } else if (u_field == 3) {                              // organique : Perlin, formes plus detaillees et plus nettes que les taches
        f = smoothstep(-0.18, 0.18, fbm(v * 4.0 + drift));
    } else if (u_field == 4) {                              // cellules : amas ronds autour des germes
        f = 1.0 - smoothstep(0.05, 0.62, worley(v * 4.0 + drift * 0.5, ph).x);
    } else if (u_field == 5) {                              // reseau : bords des cellules (ecailles, nid d'abeille)
        vec2 w = worley(v * 4.0 + drift * 0.5, ph);
        f = 1.0 - smoothstep(0.0, 0.3, w.y - w.x);
    } else if (u_field == 6) {                              // veines : cretes du bruit de gradient (lignes fines qui serpentent)
        float n = gnoise(v * 2.6 + drift) + 0.5 * gnoise(v * 5.3 - drift + 7.0);
        f = 1.0 - smoothstep(0.0, 0.16, abs(n));
    } else if (u_field == 7) {                              // marbre : domaine deforme, veines paralleles qui ondulent et coulent
        vec2 w = vec2(fbm(v * 1.5 + drift), fbm(v * 1.5 + vec2(5.2, 1.3) - drift));
        f = 0.5 + 0.5 * sin(v.x * 11.0 + v.y * 3.0 + 5.0 * fbm(v * 1.3 + 1.6 * w));
    } else {                                                // taches (origine) : bruit de valeur, grandes taches qui derivent
        f = 0.62 * vnoise(v * 3.0 + drift) + 0.38 * vnoise(v * 6.1 - drift * 1.3 + 11.0);
        f = smoothstep(0.18, 0.82, f);
    }
    f_color = vec4(clamp(f, 0.0, 1.0), 0.0, 0.0, 1.0);
}
