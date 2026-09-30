#version 330

// Passe 1, lancee DEUX fois (u_pass) : le fond (video etiree sur tout le cadre, ou motif) puis le logo
// seul avec son contour lumineux (alpha premultiplie), chacun dans son FBO. Orientation OpenGL (origine en bas).

uniform sampler2D u_video;      // rawvideo rgb24, PREMIERE ligne = haut de l'image
uniform sampler2D u_logo;       // rgba premultiplie, premiere ligne = haut
uniform vec2  u_res;            // taille du cadre en pixels
uniform vec4  u_logo_rect;      // centre x, centre y (origine HAUT gauche, 0..1), demi-largeur, demi-hauteur (uv)
uniform float u_logo_on;        // 1 si un logo est charge
uniform float u_pass;           // 0 = couche fond (opaque), 1 = couche logo seule (rgba premultiplie)
uniform float u_logo_straight;  // 1 = texture en alpha droit (logo video): premultiplie ici
uniform float u_bass;           // 0..1
uniform float u_logo_opacity;   // 0..1
uniform float u_glow;           // intensite du contour lumineux (0 = coupe)
uniform float u_glow_radius;    // multiplicateur du rayon du contour
uniform vec3  u_glow_color;

// Fond genere (u_bg_mode = 1): degrades qui defilent + damier qui bascule. Le mode "classic"
// reprend le visuel d'origine du banc de test (canaux R/V/B en dents de scie, damier de
// 80 px a 720 p); "duo" fait un degrade lisse entre deux couleurs.
uniform float u_bg_mode;       // 0 = video ffmpeg, 1 = motif
uniform float u_bg_palette;    // 0 = classic, 1 = duo
uniform vec3  u_bg_c1;
uniform vec3  u_bg_c2;
uniform vec2  u_bg_phase;      // x = defilement cumule, y = nombre de bascules du damier
uniform float u_bg_tile;       // cote d'un carreau en pixels pour 720 px de haut
uniform float u_bg_checker;    // contraste du damier
uniform float u_bg_react;      // reaction au kick (flash)
uniform float u_bg_hue;        // rotation de teinte, 0..1
uniform float u_bg_angle;      // direction du degrade duo, en degres
uniform float u_beat;          // enveloppe du kick 1 -> 0

in vec2 v_uv;
out vec4 f_color;

vec3 hue_rotate(vec3 c, float h) {
    // Rotation autour de l'axe du gris (formule de Rodrigues).
    float a = h * 6.2831853;
    vec3 k = vec3(0.57735027);
    return c * cos(a) + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - cos(a));
}

// p : position ecran, origine HAUT gauche.
vec3 background_pattern(vec2 p) {
    float tile = max(u_bg_tile * u_res.y / 720.0, 2.0);
    vec2 cell = floor(p * u_res / tile);
    float parity = mod(cell.x + cell.y + floor(u_bg_phase.y), 2.0);
    vec3 base;
    if (u_bg_palette < 0.5) {
        base = vec3(fract(p.x + u_bg_phase.x * 0.353),
                    fract(p.y + u_bg_phase.x * 0.235),
                    1.0 - p.x);
    } else {
        float a = radians(u_bg_angle);
        float t = dot(p - 0.5, vec2(cos(a), sin(a))) + 0.5 + u_bg_phase.x * 0.1;
        float tri = abs(fract(t) * 2.0 - 1.0);            // aller-retour: pas de couture
        base = mix(u_bg_c1, u_bg_c2, smoothstep(0.0, 1.0, tri));
    }
    base += parity * u_bg_checker;
    base = hue_rotate(clamp(base, 0.0, 1.0), u_bg_hue);
    base *= 1.0 + u_bg_react * 0.6 * u_beat;
    return clamp(base, 0.0, 1.0);
}

// p : position ecran, origine HAUT gauche. Renvoie le logo premultiplie (0 hors du rectangle).
vec4 logo_at(vec2 p) {
    vec2 q = (p - u_logo_rect.xy) / (2.0 * u_logo_rect.zw) + 0.5;
    float inside = step(0.0, q.x) * step(q.x, 1.0) * step(0.0, q.y) * step(q.y, 1.0);
    // Echantillonnage toujours execute (derivees valides pour les mipmaps), masque apres coup.
    vec4 c = texture(u_logo, clamp(q, 0.0, 1.0));
    c.rgb *= mix(1.0, c.a, u_logo_straight);
    return c * inside;
}

// --- Halo chrome (non audioreactif par defaut) -----------------------------------------------
// Relief = alpha du logo flou a plusieurs echelles (mipmaps, tres peu couteux); normale tiree de ce
// relief, puis reflexion d'un faux environnement de studio (bandes nettes claires/sombres) = metal.
// Le halo s'ajoute au fond en additif (comme le contour lumineux), le logo reste par-dessus.
uniform float u_chrome;          // intensite du halo (0 = coupe)
uniform float u_chrome_bump;     // relief: plus haut = chrome plus contraste
uniform float u_chrome_phase;    // temps d'animation integre cote CPU (temps x vitesse)
uniform float u_chrome_bands;    // nombre de bandes de l'environnement
uniform float u_chrome_maxlod;   // niveau de flou max (limite par la marge transparente de la texture)
uniform float u_chrome_lod0;     // log2(petit cote du logo en texels) - 8 : cale le flou sur la TAILLE du logo

// Les 6 echelles de flou vont de 1/128 a 1/4 du petit cote du logo (et jamais au-dela de la marge
// transparente): la largeur du halo ne depend donc ni de la resolution du PNG ni de sa taille a l'ecran.
float height_field(vec2 q) {
    float h = 0.0;
    float wsum = 0.0;
    for (int i = 1; i <= 6; i++) {
        float w = 1.0 - float(i) / 8.0;
        h += textureLod(u_logo, q, clamp(u_chrome_lod0 + float(i), 0.0, u_chrome_maxlod)).a * w;
        wsum += w;
    }
    return h / wsum;
}

vec3 chrome_env(vec3 r, float t) {
    float y = r.y * 0.5 + 0.5;
    float x = r.x * 0.5 + 0.5;
    float bands = sin((y * u_chrome_bands + x * 2.0 + t) * 6.2831);
    float v = smoothstep(-0.15, 0.15, bands);
    vec3 c = mix(vec3(0.02, 0.03, 0.05), vec3(0.92, 0.96, 1.00), v);
    c += pow(max(0.0, sin((y * 9.0 - x * 3.0 + t * 1.3) * 3.1415)), 24.0) * 0.8;     // reflet brillant
    c *= 0.82 + 0.18 * cos(6.2831 * (vec3(0.0, 0.33, 0.67) + y * 1.2 + t * 0.5));    // teinte irisee
    return c;
}

// q : coordonnees dans la texture du logo (marge transparente comprise). Renvoie la lumiere a ajouter.
vec3 chrome_halo(vec2 q) {
    float a = texture(u_logo, q).a;
    float e = 2.0 / float(textureSize(u_logo, 0).x);
    float h  = height_field(q);
    float dx = height_field(q + vec2(e, 0.0)) - height_field(q - vec2(e, 0.0));
    float dy = height_field(q + vec2(0.0, e)) - height_field(q - vec2(0.0, e));
    vec3 n = normalize(vec3(-dx * u_chrome_bump, -dy * u_chrome_bump, 1.0));
    vec3 chrome = chrome_env(reflect(vec3(0.0, 0.0, -1.0), n), u_chrome_phase);
    // Le halo est present autour du logo et s'estompe avec la distance; il s'eteint aussi vers le bord de
    // la texture pour ne jamais etre coupe net par son rectangle.
    vec2 edge = smoothstep(0.0, 0.08, q) * smoothstep(0.0, 0.08, 1.0 - q);
    return chrome * smoothstep(0.02, 0.25, h) * (1.0 - a) * edge.x * edge.y;
}

void main() {
    // Rawvideo arrive haut en premier : on passe en origine HAUT gauche pour lire les textures.
    vec2 p = vec2(v_uv.x, 1.0 - v_uv.y);

    if (u_pass > 0.5) {
        // Couche LOGO seule: rgb premultiplie + contour lumineux ajoute (alpha = celui du logo), pour
        // que le post-traitement la deforme independamment du fond puis la pose par-dessus.
        vec4 lg = logo_at(p) * u_logo_opacity;   // premultiplie: on pondere les 4 canaux
        vec3 rgb = lg.rgb;

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
            rgb += u_glow_color * halo * u_glow * (0.25 + 0.75 * u_bass);
        }
        if (u_chrome > 0.001) {
            vec2 q = (p - u_logo_rect.xy) / (2.0 * u_logo_rect.zw) + 0.5;
            if (q.x > 0.0 && q.x < 1.0 && q.y > 0.0 && q.y < 1.0) {
                rgb += chrome_halo(q) * u_chrome * u_logo_opacity;
            }
        }
        f_color = vec4(clamp(rgb, 0.0, 1.0), lg.a);
        return;
    }

    // Couche FOND: video ffmpeg ou motif genere.
    vec3 col = (u_bg_mode > 0.5) ? background_pattern(p) : texture(u_video, p).rgb;
    f_color = vec4(clamp(col, 0.0, 1.0), 1.0);
}
