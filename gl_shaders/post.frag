#version 330

// Passe 2 : post-traitement de l'image entiere (wobble, ripple, aberration chromatique,
// glitch) + HUD de debug. Musique coupee => tous les u_fx_* effectifs valent 0 ou les
// niveaux audio valent 0 : l'image sort quasi identique a la scene.

uniform sampler2D u_scene;
uniform vec2  u_res;
uniform float u_time;

uniform float u_bass;
uniform float u_mid;
uniform float u_high;
uniform float u_rms;
uniform float u_beat;         // enveloppe 1 -> 0 apres chaque kick
uniform float u_since_beat;   // secondes depuis le dernier kick

// Intensite effective de chaque effet (interrupteur x intensite x intensite globale).
uniform float u_fx_wobble;
uniform float u_fx_ripple;
uniform float u_fx_chroma;
uniform float u_fx_glitch;

uniform float u_hud;              // 1 = affiche les barres de debug
uniform float u_fx_state[5];      // etat des 5 interrupteurs (pour le HUD)

in vec2 v_uv;
out vec4 f_color;

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}

void main() {
    float aspect = u_res.x / u_res.y;
    vec2 uv = v_uv;
    vec2 center = vec2(0.5);

    // 1. Wobble : ondulation sinusoidale des UV, amplitude = basses.
    uv += u_fx_wobble * u_bass * 0.012 *
          vec2(sin(uv.y * 18.0 + u_time * 2.3), cos(uv.x * 14.0 + u_time * 1.9));

    // 2. Ripple : onde de choc depuis le centre, rayon = temps ecoule depuis le kick.
    vec2 d = (uv - center) * vec2(aspect, 1.0);
    float dist = length(d);
    float radius = u_since_beat * 0.9;
    float w = (dist - radius) / 0.07;
    // L'onde s'eteint sur ~1 s (plus lentement que u_beat), donc visible pendant sa course.
    float ring = exp(-w * w) * clamp(1.0 - u_since_beat, 0.0, 1.0);
    uv += (d / max(dist, 1e-4)) / vec2(aspect, 1.0) * ring * 0.05 * u_fx_ripple;

    // 3. Glitch : bandes horizontales decalees, hash quantifie dans le temps, declenche par seuil.
    float trig = max(step(0.55, u_beat) * u_beat, step(0.85, u_high) * u_high * 0.6);
    float g = trig * u_fx_glitch;
    if (g > 0.001) {
        float tq = floor(u_time * 14.0);
        float band = floor(uv.y * 26.0);
        float hb = hash(vec2(band, tq));
        if (hb > 0.72) {
            uv.x += (hash(vec2(band, tq + 7.0)) - 0.5) * 0.16 * g;
        }
    }

    // 4. Aberration chromatique : canaux R/G/B decales depuis le centre, amplitude = kick.
    vec2 off = (uv - center) * 0.022 * u_beat * u_fx_chroma;
    vec3 col;
    col.r = texture(u_scene, uv + off).r;
    col.g = texture(u_scene, uv).g;
    col.b = texture(u_scene, uv - off).b;

    // HUD : barres bass / mid / high / rms / beat en bas a gauche + 5 pastilles d'effets.
    if (u_hud > 0.5) {
        float s = max(u_res.y / 1080.0, 0.5);
        vec2 px = gl_FragCoord.xy;
        float x0 = 16.0 * s;
        float bw = 16.0 * s;
        float gap = 8.0 * s;
        float hmax = 110.0 * s;
        float levels[5] = float[5](u_bass, u_mid, u_high, u_rms, u_beat);
        vec3 colors[5] = vec3[5](vec3(1.0, 0.35, 0.3), vec3(0.4, 1.0, 0.45), vec3(0.4, 0.7, 1.0),
                                  vec3(0.9), vec3(1.0, 0.9, 0.2));
        for (int i = 0; i < 5; i++) {
            float bx = x0 + float(i) * (bw + gap);
            if (px.x >= bx && px.x < bx + bw && px.y >= 16.0 * s && px.y < 16.0 * s + hmax) {
                float fill = step(px.y - 16.0 * s, hmax * clamp(levels[i], 0.0, 1.0));
                col = mix(vec3(0.05), colors[i], fill * 0.95 + 0.0);
            }
            // pastille d'etat d'effet au-dessus de chaque barre
            float py0 = 16.0 * s + hmax + 8.0 * s;
            if (px.x >= bx && px.x < bx + bw && px.y >= py0 && px.y < py0 + bw) {
                col = mix(vec3(0.15, 0.0, 0.0), vec3(0.2, 1.0, 0.4), u_fx_state[i]);
            }
        }
    }

    f_color = vec4(clamp(col, 0.0, 1.0), 1.0);
}
