#version 330

// Foudre : un ruban par segment de l'arbre. Repere des sommets = centre de l'image, hauteur de l'image = 1 (y vers le haut),
// largeur = u_aspect ; sortie en coordonnees de clip.
uniform float u_aspect;

in vec2 in_pos;
in float in_across;     // -1 .. 1 en travers du ruban
in float in_int;        // intensite du segment

out float v_across;
out float v_int;

void main() {
    v_across = in_across;
    v_int = in_int;
    gl_Position = vec4(in_pos.x / (0.5 * u_aspect), in_pos.y / 0.5, 0.0, 1.0);
}
