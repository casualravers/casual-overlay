#version 330
// Sphere : le sommet est deplace vers l'exterieur par l'energie de sa bande de frequences (voir engine/sphere_mode.py).
// u = azimut autour de l'axe de face (0..2 pi), v = angle depuis le centre de face (0..pi) : la bande de frequences et l'historique
// ne dependent que de (u, v), jamais de la forme ; la forme dit seulement OU se trouve le sommet.
in vec2 in_uv;
uniform mat4 u_mvp;
uniform sampler2D u_hist;      // R32F : une ligne par 1/60 s (ring, repetee en y), une colonne par bande
uniform int u_bands;
uniform float u_head;          // indice de la ligne la plus recente
uniform float u_depth;         // lignes d'historique repartis sur le tour (0 = anneaux identiques)
uniform float u_relief;
uniform float u_scale;
uniform float u_point;
uniform float u_rows;
uniform float u_shape;         // 0..5 : sphere, tore, noeud de trefle, ressort, fleur, bouteille de Klein ; entre deux formes : fondu
out float v_amp;
out vec3 v_pos;
const float PI = 3.14159265;
const int SHAPES = 6;

// Une forme = un point central Q (origine, ou point de la courbe pour un tube) et un decalage O (rayon vers la surface) : P = Q + O.
// Le relief grossit O (sphere : le rayon ; tube : l'epaisseur du tube), donc il pousse toujours la surface vers l'exterieur.

// Tube autour d'une courbe C (tangente T), section d'angle w.
void tube(vec3 c, vec3 t, float rad, float w, out vec3 q, out vec3 o) {
    vec3 b = normalize(cross(t, vec3(0.31, 0.47, 0.83)));
    vec3 n = normalize(cross(b, t));
    q = c;
    o = rad * (cos(w) * n + sin(w) * b);
}

float gielis(float th, float m, float n1, float n2, float n3) {
    float a = pow(abs(cos(m * th * 0.25)), n2);
    float b = pow(abs(sin(m * th * 0.25)), n3);
    return pow(a + b + 1e-5, -1.0 / n1);
}

void shape_at(int k, float u, float v, out vec3 q, out vec3 o) {
    float w = 2.0 * v;                                                  // angle de la section d'un tube : un tour complet
    if (k == 0) {                                                       // sphere
        q = vec3(0.0);
        o = vec3(sin(v) * cos(u), sin(v) * sin(u), cos(v));
    } else if (k == 1) {                                                // tore (anneau vu de face)
        vec3 radial = vec3(cos(u), sin(u), 0.0);
        q = 0.66 * radial;
        o = 0.30 * (cos(w) * radial + sin(w) * vec3(0.0, 0.0, 1.0));
    } else if (k == 2) {                                                // noeud de trefle
        vec3 c = 0.3 * vec3(sin(u) + 2.0 * sin(2.0 * u), cos(u) - 2.0 * cos(2.0 * u), -sin(3.0 * u));
        vec3 t = 0.3 * vec3(cos(u) + 4.0 * cos(2.0 * u), -sin(u) + 4.0 * sin(2.0 * u), -3.0 * cos(3.0 * u));
        tube(c, t, 0.2, w, q, o);
    } else if (k == 3) {                                                // ressort enroule en anneau
        float r = 0.62 + 0.21 * cos(7.0 * u);
        vec3 c = vec3(r * cos(u), r * sin(u), 0.21 * sin(7.0 * u));
        vec3 t = vec3(-1.47 * sin(7.0 * u) * cos(u) - r * sin(u), -1.47 * sin(7.0 * u) * sin(u) + r * cos(u), 1.47 * cos(7.0 * u));
        tube(c, t, 0.13, w, q, o);
    } else if (k == 4) {                                                // fleur (superformule de Gielis)
        float th = u - PI;
        float ph = v - 0.5 * PI;
        float r1 = gielis(th, 7.0, 0.55, 1.1, 1.1);
        float r2 = gielis(ph * 2.0, 4.0, 1.0, 1.4, 1.4);
        q = vec3(0.0);
        o = vec3(sin(v) * cos(u), sin(v) * sin(u), cos(v)) * clamp(1.05 * r1 * r2, 0.25, 1.15);
    } else {                                                            // bouteille de Klein (immersion en huit)
        float h = 0.5 * u;
        float s = 0.4 * (2.0 + cos(h) * sin(w) - sin(h) * sin(2.0 * w));
        q = 0.8 * vec3(cos(u), sin(u), 0.0);
        o = vec3(s * cos(u), s * sin(u), 0.4 * (sin(h) * sin(w) + cos(h) * sin(2.0 * w))) - q;
    }
}

void main() {
    float psi = in_uv.x * 2.0 * PI;
    float alpha = in_uv.y * PI;
    float m = min(alpha, PI - alpha) / (0.5 * PI);                     // 0 au centre de face (et de dos), 1 sur le bord
    float tau = abs(psi - PI) / PI;                                    // 0..1, symetrique : pas de couture
    float x = (m * float(max(u_bands - 1, 0)) + 0.5) / 64.0;
    float y = (u_head + 0.5 - tau * u_depth) / u_rows;
    float amp = texture(u_hist, vec2(x, y)).r;
    v_amp = amp;
    float s = clamp(u_shape, 0.0, float(SHAPES - 1));
    int k = int(min(floor(s), float(SHAPES - 2)));
    float f = s - float(k);
    f = f * f * (3.0 - 2.0 * f);                                        // fondu doux entre deux formes
    vec3 q0, o0, q1, o1;
    shape_at(k, psi, alpha, q0, o0);
    shape_at(k + 1, psi, alpha, q1, o1);
    vec3 q = mix(q0, q1, f);
    vec3 o = mix(o0, o1, f);
    v_pos = u_scale * (q + o * (1.0 + u_relief * 0.9 * amp));
    gl_Position = u_mvp * vec4(v_pos, 1.0);
    gl_PointSize = u_point;
}
