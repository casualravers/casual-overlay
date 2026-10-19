#version 330

// Doodle : texture a alpha PREMULTIPLIE (comme le logo), fondu par u_alpha. Melange (1, 1 - alpha_source) cote Python : pas de halo sombre.
in vec2 v_tex;
uniform sampler2D u_tex;
uniform float u_alpha;
out vec4 f_color;

void main() {
    f_color = texture(u_tex, v_tex) * u_alpha;
}