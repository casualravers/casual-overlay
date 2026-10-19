#version 330

// Snap, style pencil, dessine en GL (equivalent de paint_pencil_columns d'audio2wave_snap.py).
// Les donnees d'une photo sont des hauteurs de colonnes entieres (u_cur / u_prev, R32F, une rangee par grandeur) :
//   rangee 0 = haut de l'enveloppe, 1 = bas, 2 / 3 = hauteurs a encrer (3 = 2 hors "une seule ligne"), 4 = halo des kicks (0..1),
//   5 = epaississement du trait (px), 6 = portee verticale du halo (px) : arrondis cote Python (round pair, comme audio2wave)
// Origine de l'image en HAUT : gl_FragCoord.y = rangee de l'image (la texture de sortie est lue "premiere ligne en haut").
in vec2 v_uv;
out vec4 f_color;

uniform sampler2D u_cur;
uniform sampler2D u_prev;
uniform sampler2D u_vid;        // video interieure (RGB, premiere ligne = haut)
uniform sampler2D u_vid_out;    // video exterieure
uniform ivec2  u_size;
uniform int    u_drawn;         // colonnes deja revelees : x < u_drawn -> photo courante, sinon la precedente
uniform int    u_ndraw;         // 1 (--wave) ou 2 (haut et bas de l'enveloppe) hauteurs a encrer
uniform int    u_thick;
uniform vec3   u_bg;
uniform vec3   u_ink;
uniform vec3   u_glow_col;
uniform int    u_live_extra;    // halo temps reel : epaississement et portee, deja arrondis cote Python (round pair)
uniform int    u_live_halo;
uniform float  u_live;         // < 0 : halo par kicks (rangee 4) ; >= 0 : intensite temps reel pour toutes les colonnes
uniform int    u_prev_ink;      // 1 : la photo precedente garde son trait
uniform int    u_has_vid;
uniform int    u_has_out;

float fetch(sampler2D t, int row, int x) {
    return texelFetch(t, ivec2(clamp(x, 0, u_size.x - 1), row), 0).r;
}

// Equivalent de Python round() (arrondi pair sur les demi-valeurs : le rayon x le halo en donne souvent)
int ri(float v) { return int(roundEven(v)); }

vec3 column(sampler2D t, bool ink_on, int x, int y) {
    int xp = max(x - 1, 0);
    int top = max(0, min(ri(fetch(t, 0, x)), ri(fetch(t, 0, xp))));
    int bottom = min(u_size.y - 1, max(ri(fetch(t, 1, x)), ri(fetch(t, 1, xp))));
    vec3 col = u_bg;
    if (u_has_vid != 0 && y >= top && y <= bottom) {
        col = texelFetch(u_vid, ivec2(x, y), 0).rgb;
    }
    if (u_has_out != 0 && (y < top || y > bottom)) {
        col = texelFetch(u_vid_out, ivec2(x, y), 0).rgb;
    }
    if (ink_on) {
        float glow = u_live >= 0.0 ? u_live : fetch(t, 4, x);
        int extra = u_live >= 0.0 ? u_live_extra : ri(fetch(t, 5, x));
        int halo_px = u_live >= 0.0 ? u_live_halo : ri(fetch(t, 6, x));
        for (int i = 0; i < 2; i++) {
            if (i >= u_ndraw) break;
            int y_now = ri(fetch(t, 2 + i, x));
            int y_prev = ri(fetch(t, 2 + i, xp));
            int lo = min(y_now, y_prev) - extra;
            int hi = max(y_now, y_prev) + u_thick + extra;     // [lo, hi) encre
            if (halo_px > 0) {
                // degrade vers la couleur du halo juste au-dela du trait (dans le fond, jamais sur le trait lui-meme)
                int dy = y < lo ? lo - y : y - (hi - 1);
                if (dy >= 1 && dy <= halo_px) {
                    float tt = glow * (1.0 - float(dy) / float(halo_px));
                    if (tt > 0.0) col = mix(col, u_glow_col, tt);
                }
            }
            if (y >= lo && y < hi) col = u_ink;
        }
    }
    return col;
}

void main() {
    int x = int(gl_FragCoord.x);
    int y = int(gl_FragCoord.y);
    vec3 c = x < u_drawn ? column(u_cur, true, x, y) : column(u_prev, u_prev_ink != 0, x, y);
    f_color = vec4(c, 1.0);
}
