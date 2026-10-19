#version 330

// Snap, styles simple et rekordbox, dessines en GL (equivalent de showwavespic + overlay d'audio2wave_snap.py).
// u_cur / u_prev : R32F, une colonne par colonne du trace (u_cols de large, agrandi au plus proche voisin), une rangee par trace
// (0 = signal complet, 1 = passe-bas 2 kHz, 2 = passe-bas 200 Hz) ; valeur = demi-hauteur de la barre en pixels (autour du centre).
// Les traces s'empilent du plus large au plus etroit : le plus etroit se pose dans le plus large (rekordbox).
// Origine de l'image en HAUT (gl_FragCoord.y = rangee de l'image).
in vec2 v_uv;
out vec4 f_color;

uniform sampler2D u_cur;
uniform sampler2D u_prev;
uniform ivec2 u_size;
uniform int   u_cols;        // largeur reelle du trace avant agrandissement (photo courante)
uniform int   u_cols_prev;   // idem pour la photo precedente
uniform int   u_layers;      // 1 (simple) ou 3 (rekordbox)
uniform int   u_drawn;
uniform vec3  u_bg;
uniform vec3  u_c0;          // trace 0 (le plus large)
uniform vec3  u_c1;
uniform vec3  u_c2;

vec3 draw(sampler2D t, int cols, int x, int y) {
    int cx = min(((2 * x + 1) * cols) / (2 * u_size.x), cols - 1);   // colonne source : plus proche voisin, centre du pixel (comme ffmpeg)
    float c = float(u_size.y) * 0.5;
    vec3 col = u_bg;
    for (int k = 0; k < 3; k++) {
        if (k >= u_layers) break;
        float h = texelFetch(t, ivec2(cx, k), 0).r;
        if (float(y) >= floor(c - h) && float(y) < floor(c + h)) {
            col = k == 0 ? u_c0 : (k == 1 ? u_c1 : u_c2);
        }
    }
    return col;
}

void main() {
    int x = int(gl_FragCoord.x);
    int y = int(gl_FragCoord.y);
    f_color = vec4(x < u_drawn ? draw(u_cur, u_cols, x, y) : draw(u_prev, u_cols_prev, x, y), 1.0);
}
