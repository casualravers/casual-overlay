#version 330

// Foudre : coeur blanc + corps de la couleur choisie, additifs (le halo vient du flou par mipmaps dans post.frag).
uniform vec3 u_color;

in float v_across;
in float v_int;
out vec4 frag;

void main() {
    float a = 1.0 - abs(v_across);
    float core = a * a * a;
    frag = vec4(vec3(core) + u_color * a, 1.0) * v_int;
}
