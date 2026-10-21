#version 330
in float v_amp;
in vec3 v_pos;
uniform int u_style;           // 0 fil de fer, 1 surface eclairee, 2 points
uniform vec3 u_c0;
uniform vec3 u_c1;
uniform vec3 u_c2;
uniform int u_ncol;
out vec4 f_color;

vec3 ramp(float t) {
    t = clamp(t, 0.0, 1.0);
    if (u_ncol <= 1) return u_c0;
    if (u_ncol == 2) return mix(u_c0, u_c1, t);
    return t < 0.5 ? mix(u_c0, u_c1, t * 2.0) : mix(u_c1, u_c2, t * 2.0 - 1.0);
}

void main() {
    vec3 base = ramp(v_amp * 1.4);
    if (u_style == 1) {
        vec3 n = normalize(cross(dFdx(v_pos), dFdy(v_pos)));
        if (n.z < 0.0) n = -n;                                          // formes non convexes : eclairage a deux faces, normale vers la camera
        float lit = max(dot(n, normalize(vec3(-0.4, 0.6, 0.7))), 0.0);
        float rim = pow(1.0 - max(n.z, 0.0), 2.0);
        f_color = vec4(base * (0.18 + 0.82 * lit) + base * 0.25 * rim, 1.0);
    } else {
        float depth = clamp(0.5 + 0.5 * v_pos.z / 1.2, 0.0, 1.0);       // la face arriere est plus sombre
        float glow = 0.35 + 0.65 * depth;
        if (u_style == 2) {
            vec2 q = gl_PointCoord * 2.0 - 1.0;
            glow *= clamp(1.0 - dot(q, q), 0.0, 1.0);
        }
        f_color = vec4(base * glow * (0.55 + 0.7 * v_amp), 1.0);
    }
}
