#version 330

// Ridge, dessine en GL. Deux passes dans le meme fichier (u_pass) vers le canevas persistant :
//   0 = decalage : le canevas existant monte de u_spacing rangees (shift_canvas), le bas repasse au fond
//   1 = ligne    : colonnes [u_x0, u_x1) de la nouvelle ligne (paint_ridge_line) : fond de la crete jusqu'a la base, trait d'encre
//                  autour du pic ; ce qui est au-dessus de la crete n'est pas touche (discard), c'est ce qui occulte les anciennes lignes
// Origine de l'image en HAUT (gl_FragCoord.y = rangee de l'image).
in vec2 v_uv;
out vec4 f_color;

uniform int       u_pass;
uniform sampler2D u_src;       // pass 0 : canevas a decaler
uniform sampler2D u_heights;   // pass 1 : R32F, une rangee, la hauteur du pic de chaque colonne
uniform ivec2     u_size;
uniform int       u_spacing;
uniform int       u_thick;
uniform int       u_x0;
uniform int       u_x1;
uniform vec3      u_bg;
uniform vec3      u_ink;

void main() {
    int x = int(gl_FragCoord.x);
    int y = int(gl_FragCoord.y);
    if (u_pass == 0) {
        f_color = (y + u_spacing < u_size.y) ? vec4(texelFetch(u_src, ivec2(x, y + u_spacing), 0).rgb, 1.0) : vec4(u_bg, 1.0);
        return;
    }
    if (x < u_x0 || x >= u_x1) discard;
    int now = int(floor(texelFetch(u_heights, ivec2(x, 0), 0).r + 0.5));
    int prev = int(floor(texelFetch(u_heights, ivec2(max(x - 1, 0), 0), 0).r + 0.5));
    int top = min(prev, now);
    if (y < top) discard;
    int stroke_bottom = min(u_size.y - 1, max(prev, now) + u_thick - 1);
    f_color = vec4(y <= stroke_bottom ? u_ink : u_bg, 1.0);
}
