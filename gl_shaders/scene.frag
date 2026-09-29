#version 330

// Passe 1 : video (etiree sur tout le cadre) + logo (alpha premultiplie) + contour lumineux.
// Sortie dans le FBO "scene", orientation OpenGL (origine en bas).

uniform sampler2D u_video;      // rawvideo rgb24, PREMIERE ligne = haut de l'image
uniform sampler2D u_logo;       // rgba premultiplie, premiere ligne = haut
uniform vec2  u_res;            // taille du cadre en pixels
uniform vec4  u_logo_rect;      // centre x, centre y (origine HAUT gauche, 0..1), demi-largeur, demi-hauteur (uv)
uniform float u_logo_on;        // 1 si un logo est charge
uniform float u_bass;           // 0..1
uniform float u_logo_opacity;   // 0..1
uniform float u_glow;           // intensite du contour lumineux (0 = coupe)
uniform float u_glow_radius;    // multiplicateur du rayon du contour
uniform vec3  u_glow_color;

in vec2 v_uv;
out vec4 f_color;

// p : position ecran, origine HAUT gauche. Renvoie le logo premultiplie (0 hors du rectangle).
vec4 logo_at(vec2 p) {
    vec2 q = (p - u_logo_rect.xy) / (2.0 * u_logo_rect.zw) + 0.5;
    float inside = step(0.0, q.x) * step(q.x, 1.0) * step(0.0, q.y) * step(q.y, 1.0);
    // Echantillonnage toujours execute (derivees valides pour les mipmaps), masque apres coup.
    return texture(u_logo, clamp(q, 0.0, 1.0)) * inside;
}

void main() {
    // Rawvideo arrive haut en premier : on passe en origine HAUT gauche pour lire les textures.
    vec2 p = vec2(v_uv.x, 1.0 - v_uv.y);
    vec3 col = texture(u_video, p).rgb;

    if (u_logo_on > 0.5) {
        vec4 lg = logo_at(p) * u_logo_opacity;   // premultiplie: on pondere les 4 canaux

        // Contour lumineux : moyenne de l'alpha sur deux anneaux autour du pixel, gardee hors du logo.
        if (u_glow > 0.001) {
            float radius = mix(0.006, 0.03, u_bass) * u_glow_radius;
            vec2 asp = vec2(u_res.y / u_res.x, 1.0);
            float acc = 0.0;
            for (int i = 0; i < 12; i++) {
                float ang = float(i) * 0.5235988;   // 2*pi/12
                vec2 dir = vec2(cos(ang), sin(ang)) * asp;
                acc += logo_at(p + dir * radius).a;
                acc += logo_at(p + dir * radius * 0.5).a;
            }
            float halo = clamp(acc / 24.0 * 1.6, 0.0, 1.0) * (1.0 - lg.a);
            col += u_glow_color * halo * u_glow * (0.25 + 0.75 * u_bass);
        }

        // Alpha premultiplie : pas de halo sombre sur les bords.
        col = lg.rgb + col * (1.0 - lg.a);
    }

    f_color = vec4(clamp(col, 0.0, 1.0), 1.0);
}
