#version 330

// Passe "champ holographique", a BASSE resolution (1/4 de l'ecran) : lit l'alpha de la couche logo a de
// nombreuses echelles de flou (mipmaps a la taille de l'ecran) et ecrit, pour chaque texel,
//     r = v  : champ en echelle logarithmique, 0 (hors de portee) .. 1 (contre le logo)
//     g, b   : gradient de v (pointe vers le logo)
//     a      : densite locale du logo (0 hors du logo, 1 en plein dedans)
// Le post-traitement (post.frag) n'a plus qu'a lire cette texture en bilineaire : bien plus lisse que
// d'echantillonner les mipmaps a pleine resolution (qui donnent des contours carres) et ~16 fois moins de
// lectures.

uniform sampler2D u_logo;       // couche logo (rgba), mipmaps construites
uniform vec2  u_res;            // taille de l'ecran en pixels (pas celle de cette passe)
uniform float u_holo_reach;     // portee: le champ est agrandi d'autant autour du logo (homothetie)
uniform vec2  u_holo_center;    // centre du logo (uv, origine bas gauche)
uniform float u_holo_lod0;      // log2(hauteur de l'ecran / 720)

in vec2 v_uv;
out vec4 f_color;

// Alpha du logo au niveau de flou `lod`. Aux gros niveaux, 4 prises decalees (en croix tournee selon
// le niveau) cassent la structure en blocs des mipmaps et arrondissent les contours.
float blur_alpha(vec2 uv, float lod, float rot) {
    if (lod < 2.5) {
        return textureLod(u_logo, uv, lod).a;
    }
    vec2 r = 0.75 * exp2(lod) / u_res;
    float acc = 0.0;
    for (int k = 0; k < 4; k++) {
        float a = rot + float(k) * 1.5707963;
        acc += textureLod(u_logo, uv + vec2(cos(a), sin(a)) * r, lod).a;
    }
    return acc * 0.25;
}

// Champ = moyenne ponderee de l'alpha flou a `count` echelles (la premiere a `first`, puis +1,4 a chaque pas).
float holo_field(vec2 uv, float first, float count) {
    float f = 0.0;
    float ws = 0.0;
    float lod0 = u_holo_lod0;
    for (int i = 0; i < 7; i++) {
        if (float(i) >= count) break;
        // bornee: au-dela de ~0,4 hauteur d'ecran les mipmaps n'apportent qu'un plateau (moyenne de l'image)
        float lod = clamp(lod0 + first + float(i) * 1.4, 0.0, u_holo_lod0 + 8.0);
        float w = 1.0 / (1.0 + 0.3 * float(i));
        f += blur_alpha(uv, lod, 0.6 + float(i) * 0.9) * w;
        ws += w;
    }
    return f / ws;
}

// Echelle logarithmique: l'alpha flou decroit tres vite avec la distance, le log le rend lineaire et
// donne au halo toute sa portee.
float holo_v(vec2 uv, float first, float count) {
    float f = holo_field(uv, first, count);
    float v = clamp(log(max(f, 1e-5) / 0.003) / log(0.30 / 0.003), 0.0, 1.0);
    // Fondu radial autour du logo (en hauteurs d'ecran, dans l'espace de base avant l'homothetie de la
    // portee): le halo ne peut jamais couvrir l'ecran entier ni laisser un bord dur.
    float dist = length((uv - u_holo_center) * vec2(u_res.x / u_res.y, 1.0));
    return v * (1.0 - smoothstep(0.30, 0.75, dist));
}

void main() {
    // Portee: on lit le champ de base en ramenant le point vers le centre du logo (homothetie de rapport
    // 1/reach) : le halo entier grandit d'autant, de facon monotone (changer l'echelle du flou ne le ferait pas).
    vec2 uv = u_holo_center + (v_uv - u_holo_center) / u_holo_reach;
    float v = holo_v(uv, 1.5, 7.0);
    vec2 pxs = 1.0 / u_res;
    float es = 10.0 * exp2(u_holo_lod0);                                  // pas du gradient, en pixels
    vec2 ex = vec2(es * pxs.x, 0.0);
    vec2 ey = vec2(0.0, es * pxs.y);
    vec2 g = vec2(holo_v(uv + ex, 3.0, 4.0) - holo_v(uv - ex, 3.0, 4.0),
                  holo_v(uv + ey, 3.0, 4.0) - holo_v(uv - ey, 3.0, 4.0));
    // Densite locale du logo (flou d'environ 10 px a 720 p) a la vraie position: sert a eteindre la lumiere et
    // la deformation DANS le logo (les trous d'un logo a traits fins, sinon, laisseraient voir le halo au
    // travers et le rendraient illisible).
    float own = blur_alpha(v_uv, max(u_holo_lod0 + 3.8, 0.0), 0.0);
    f_color = vec4(v, g / u_holo_reach, own);      // gradient ramene a l'echelle de l'ecran
}
