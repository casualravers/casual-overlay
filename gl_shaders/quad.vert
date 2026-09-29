#version 330

// Quad plein cadre (triangle strip de 4 sommets en coordonnees ecran -1..1).
in vec2 in_pos;
out vec2 v_uv;   // 0..1, origine en BAS a gauche (convention OpenGL)

void main() {
    v_uv = in_pos * 0.5 + 0.5;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
