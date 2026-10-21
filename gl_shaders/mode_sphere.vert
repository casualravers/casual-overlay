#version 330
// Sphere : le sommet est deplace vers l'exterieur par l'energie de sa bande de frequences (voir engine/sphere_mode.py).
// u = azimut autour de l'axe de face, v = angle depuis le centre de face (0 = centre, 1 = arriere).
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
out float v_amp;
out vec3 v_pos;
uniform float u_shape;         // 0 sphere, 1 cube, 2 octaedre, 3 cylindre, 4 etoile : le curseur mele deux formes voisines
const float PI = 3.14159265;

// Rayon de chaque forme dans la direction d (formes etoilees autour du centre) ; la meme direction garde sa bande de frequences.
float shape_at(int k, vec3 d, float psi) {
    vec3 a = abs(d) + 1e-4;
    if (k == 0) return 1.0;
    if (k == 1) return 0.78 / max(a.x, max(a.y, a.z));
    if (k == 2) return 1.15 / (a.x + a.y + a.z);
    if (k == 3) return min(0.82 / length(a.xy), 0.82 / a.z);
    return 0.82 + 0.45 * pow(abs(cos(3.0 * psi)), 3.0) * sin(acos(clamp(d.z, -1.0, 1.0)));
}

float shape_radius(vec3 d, float psi) {
    float s = clamp(u_shape, 0.0, 4.0);
    int k = int(min(floor(s), 3.0));
    float f = s - float(k);
    f = f * f * (3.0 - 2.0 * f);                                        // fondu doux entre deux formes
    return mix(shape_at(k, d, psi), shape_at(k + 1, d, psi), f);
}

void main() {
    float psi = in_uv.x * 2.0 * PI;
    float alpha = in_uv.y * PI;
    vec3 d = vec3(sin(alpha) * cos(psi), sin(alpha) * sin(psi), cos(alpha));
    float m = min(alpha, PI - alpha) / (0.5 * PI);                     // 0 au centre de face (et de dos), 1 sur le bord
    float tau = abs(psi - PI) / PI;                                    // 0..1, symetrique : pas de couture
    float x = (m * float(max(u_bands - 1, 0)) + 0.5) / 64.0;
    float y = (u_head + 0.5 - tau * u_depth) / u_rows;
    float amp = texture(u_hist, vec2(x, y)).r;
    v_amp = amp;
    float r = u_scale * shape_radius(d, psi) * (1.0 + u_relief * 0.9 * amp);
    v_pos = d * r;
    gl_Position = u_mvp * vec4(v_pos, 1.0);
    gl_PointSize = u_point;
}
