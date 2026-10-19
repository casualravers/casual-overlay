#version 330

// Doodle : un quad texture pose dans un rectangle de l'ecran. u_rect = (x, y, largeur, hauteur) en fractions de l'ecran, origine en HAUT a gauche.
in vec2 in_pos;
uniform vec4 u_rect;
out vec2 v_tex;   // origine en haut a gauche : la premiere ligne de la texture est le haut de l'image

void main() {
    vec2 uv = in_pos * 0.5 + 0.5;                        // 0..1, origine en bas a gauche
    v_tex = vec2(uv.x, 1.0 - uv.y);
    vec2 top = vec2(u_rect.x + uv.x * u_rect.z, u_rect.y + (1.0 - uv.y) * u_rect.w);
    gl_Position = vec4(top.x * 2.0 - 1.0, 1.0 - top.y * 2.0, 0.0, 1.0);
}