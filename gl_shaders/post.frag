#version 330

// Passe 2 : post-traitement de l'image entiere (wobble, ripple, aberration chromatique,
// glitch) + HUD de debug. Musique coupee => tous les u_fx_* effectifs valent 0 ou les
// niveaux audio valent 0 : l'image sort quasi identique a la scene.

uniform sampler2D u_scene;    // couche FOND (opaque)
uniform sampler2D u_logo;     // couche LOGO (rgba premultiplie, transparente hors logo)
uniform vec2  u_res;
uniform float u_time;

uniform float u_bass;
uniform float u_mid;
uniform float u_high;
uniform float u_rms;
uniform float u_beat;         // enveloppe 1 -> 0 apres chaque kick
uniform float u_since_beat;   // secondes depuis le dernier kick

// Intensite effective de chaque effet (interrupteur x intensite x intensite globale), une fois par
// couche : x = wobble, y = ripple, z = aberration chromatique, w = glitch. Avec des reglages identiques
// et un centre commun, le resultat est celui d'un post-traitement unique sur l'image composee.
uniform vec4  u_fx_bg;
uniform vec4  u_fx_logo;
uniform vec2  u_center_logo;  // centre de l'onde de choc / de l'aberration sur la couche logo (origine bas gauche)
uniform float u_salt_logo;    // decale le hasard du glitch du logo (0 = memes bandes que le fond)
uniform float u_logo_on;

// Halo holographique: lumiere irisee a grande portee qui deforme le FOND autour du logo (le logo reste
// intact) et y traine de la poussiere d'etoiles. u_holo = 0 => coupe.
uniform float u_holo;         // interrupteur x reaction a l'audio (1 = normal, 0 = coupe)
uniform float u_holo_light;   // intensite de la lumiere irisee
uniform float u_holo_reach;   // portee (multiplicateur de l'etendue)
uniform float u_holo_warp;    // deformation du fond
uniform float u_holo_dust;    // quantite de poussiere d'etoiles
uniform float u_holo_phase;   // temps d'animation integre cote CPU
uniform vec2  u_holo_center;  // centre du logo (uv, origine bas gauche): origine de la derive de la poussiere
uniform sampler2D u_holo_tex; // champ holographique (holo.frag): r = distance log, gb = gradient

uniform float u_hud;              // 1 = affiche les barres de debug
uniform float u_fx_state[5];      // etat des 5 interrupteurs (pour le HUD)

in vec2 v_uv;
out vec4 f_color;

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}

// Les quatre effets appliques a UNE couche (texture premultipliee: les 4 canaux suivent les memes UV).
// Aberration chromatique: chaque canal de couleur vient de son propre decalage; l'alpha est le plus
// grand des trois, pour ne pas laisser de frange sombre sur les bords d'un logo.
vec4 layer_fx(sampler2D tex, vec4 fx, vec2 center, float salt, vec2 disp) {
    float aspect = u_res.x / u_res.y;
    vec2 uv = v_uv;

    // 1. Wobble : ondulation sinusoidale des UV, amplitude = basses.
    uv += fx.x * u_bass * 0.012 *
          vec2(sin(uv.y * 18.0 + u_time * 2.3), cos(uv.x * 14.0 + u_time * 1.9));

    // 2. Ripple : onde de choc depuis le centre, rayon = temps ecoule depuis le kick.
    vec2 d = (uv - center) * vec2(aspect, 1.0);
    float dist = length(d);
    float radius = u_since_beat * 0.9;
    float w = (dist - radius) / 0.07;
    // L'onde s'eteint sur ~1 s (plus lentement que u_beat), donc visible pendant sa course.
    float ring = exp(-w * w) * clamp(1.0 - u_since_beat, 0.0, 1.0);
    uv += (d / max(dist, 1e-4)) / vec2(aspect, 1.0) * ring * 0.05 * fx.y;

    // 3. Glitch : bandes horizontales decalees, hash quantifie dans le temps, declenche par seuil.
    float trig = max(step(0.55, u_beat) * u_beat, step(0.85, u_high) * u_high * 0.6);
    float g = trig * fx.w;
    if (g > 0.001) {
        float tq = floor(u_time * 14.0) + salt;
        float band = floor(uv.y * 26.0);
        float hb = hash(vec2(band, tq));
        if (hb > 0.72) {
            uv.x += (hash(vec2(band, tq + 7.0)) - 0.5) * 0.16 * g;
        }
    }

    // 4. Aberration chromatique : canaux R/G/B decales depuis le centre, amplitude = kick.
    // `disp` (halo holographique) deforme le fond avec une legere dispersion spectrale: le rouge est
    // pousse plus loin que le bleu, d'ou les franges irisees. Toujours le niveau 0 des mipmaps (la couche
    // logo en a pour le halo): rien n'est flou.
    vec2 off = (uv - center) * 0.022 * u_beat * fx.z;
    vec4 sr = textureLod(tex, uv + off + disp * 1.35, 0.0);
    vec4 sg = textureLod(tex, uv + disp, 0.0);
    vec4 sb = textureLod(tex, uv - off + disp * 0.65, 0.0);
    return vec4(sr.r, sg.g, sb.b, max(sr.a, max(sg.a, sb.a)));
}

// --- Halo holographique --------------------------------------------------------------------
// Le champ (distance "logarithmique" au logo + gradient) est calcule a basse resolution par holo.frag
// et lu ici en bilineaire. Le gradient donne la direction "vers le logo" : il sert de lentille
// (deformation du fond), de direction de derive pour la poussiere d'etoiles et de teinte irisee.
vec3 holo_palette(float t) {
    return 0.5 + 0.5 * cos(6.2831853 * (vec3(0.0, 0.33, 0.67) + t));
}

// Poussiere d'etoiles sur une grille POLAIRE centree sur le logo : chaque anneau a son nombre de
// cellules, et tout l'ensemble derive vers l'exterieur (coordonnee radiale - temps), d'ou des etoiles
// qui s'eloignent du logo en laissant une courte traine radiale. Stable et lisse (aucune dependance au
// gradient du champ, qui serait bruite).
float dust_layer(vec2 px, vec2 c, float cell, float stretch, float speed, float seed) {
    vec2 d = px - c;
    float r = length(d);
    float a = r / (cell * stretch) - u_holo_phase * speed;
    float ia = floor(a);
    float ring_r = max((ia + u_holo_phase * speed + 0.5) * cell * stretch, cell);
    float n = max(floor(6.2831853 * ring_r / cell), 6.0);                   // cellules sur cet anneau
    float b = atan(d.y, d.x) / 6.2831853 * n;
    vec2 id = vec2(ia, floor(b));
    float h = hash(id + seed);
    float present = step(0.78, h);
    vec2 o = (vec2(hash(id + seed + 3.1), hash(id + seed + 7.7)) - 0.5) * 0.5;
    vec2 f = vec2(fract(a), fract(b)) - 0.5 - o;
    // traine : le coeur est etire le long du rayon (coordonnee a), court en travers
    float core = smoothstep(0.34, 0.0, length(vec2(f.x / 1.0, f.y * 1.0 * stretch / 1.8)));
    float twinkle = 0.45 + 0.55 * sin(u_time * (1.5 + 3.0 * h) + h * 40.0);
    return present * core * twinkle;
}
void holo(vec2 uv, out vec2 disp, out vec3 light) {
    vec4 field = texture(u_holo_tex, uv);
    float v = field.r;
    vec2 g = field.gb;
    // Pas de lumiere ni de deformation DANS le logo (field.a = sa densite locale): un logo a traits fins
    // reste net, le halo ne s'etale qu'autour de lui.
    float open_ = 1.0 - smoothstep(0.015, 0.16, field.a);
    vec2 toward = g / max(length(g), 1e-5);                               // vers le logo
    float ph = u_holo_phase * 6.2831853;
    float env = smoothstep(0.0, 0.55, v) * (1.0 - 0.35 * v);             // nul loin (sans bord net), fort pres du logo

    // Deformation du fond: ondes concentriques qui se propagent depuis le logo (le fond est pousse
    // vers / loin du logo selon la phase), comme une lentille holographique.
    float wave = sin(v * 24.0 - ph * 2.0);
    disp = toward * u_holo_warp * u_holo * 0.028 * wave * env * open_ * vec2(1.0, u_res.x / u_res.y);

    // Lumiere irisee: franges (courbes de niveau du champ) sur un voile doux; teinte = distance +
    // direction (sans coupure d'angle) + temps.
    float hue = v * 1.4 + u_holo_phase * 1.5 + toward.x * 0.22 + toward.y * 0.12;
    vec3 irid = holo_palette(hue);
    float fringe = pow(0.5 + 0.5 * sin(v * 30.0 - ph * 3.0), 3.0);
    light = irid * (0.32 * env + 1.5 * fringe * env * env) * u_holo_light * u_holo * open_;

    // Poussiere d'etoiles : deux couches (fine et large) qui derivent le long des lignes de champ,
    // allongees en traine, plus denses pres du logo.
    vec2 px = uv * u_res;
    float scale = u_res.y / 720.0;
    vec2 c = u_holo_center * u_res;
    float dust = dust_layer(px, c, 20.0 * scale, 2.6, 0.9, 0.0) + 0.7 * dust_layer(px, c, 36.0 * scale, 2.2, 0.5, 11.0);
    light += holo_palette(hue + 0.3) * dust * (0.25 + 0.75 * v) * smoothstep(0.0, 0.25, v) * u_holo_dust * u_holo * 2.0 * open_;
}
void main() {
    // Halo holographique: deformation du fond + lumiere (jamais sur le logo: pose dessus, intact).
    vec2 disp = vec2(0.0);
    vec3 light = vec3(0.0);
    if (u_holo > 0.001 && u_logo_on > 0.5) {
        holo(v_uv, disp, light);
    }
    // Fond et logo deformes chacun avec leurs reglages, puis logo pose par-dessus (alpha premultiplie).
    vec3 col = layer_fx(u_scene, u_fx_bg, vec2(0.5), 0.0, disp).rgb + light;
    if (u_logo_on > 0.5) {
        vec4 lg = layer_fx(u_logo, u_fx_logo, u_center_logo, u_salt_logo, vec2(0.0));
        col = lg.rgb + col * (1.0 - lg.a);
    }

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
