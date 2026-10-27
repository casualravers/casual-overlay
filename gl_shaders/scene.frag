#version 330

// Passe 1, lancee DEUX fois (u_pass) : le fond (video etiree sur tout le cadre, ou motif) puis le logo
// seul avec son contour lumineux (alpha premultiplie), chacun dans son FBO. Orientation OpenGL (origine en bas).

uniform sampler2D u_video;      // rawvideo rgb24, PREMIERE ligne = haut de l'image
uniform sampler2D u_logo;       // rgba premultiplie, premiere ligne = haut
uniform vec2  u_res;            // taille du cadre en pixels
uniform vec4  u_logo_rect;      // centre x, centre y (origine HAUT gauche, 0..1), demi-largeur, demi-hauteur (uv)
uniform vec2  u_logo_cs;         // cos, sin de la rotation autour du centre (seuls les doodles pivotent ; sin = 0 : droit)
uniform float u_logo_on;        // 1 si un logo est charge
uniform float u_pass;           // 0 = couche fond (opaque), 1 = couche logo seule (rgba premultiplie)
uniform float u_logo_straight;  // 1 = texture en alpha droit (logo video): premultiplie ici
uniform float u_bass;           // 0..1
uniform float u_logo_opacity;   // 0..1
uniform float u_glow;           // intensite du contour lumineux (0 = coupe)
uniform float u_glow_radius;    // multiplicateur du rayon du contour
uniform vec3  u_glow_color;

// Fond genere (u_bg_mode = 1): degrades qui defilent + damier qui bascule. Le mode "classic"
// reprend le visuel d'origine du banc de test (canaux R/V/B en dents de scie, damier de
// 80 px a 720 p); "duo" fait un degrade lisse entre deux couleurs.
uniform float u_bg_mode;       // 0 = video ffmpeg, 1 = motif
uniform float u_bg_palette;    // 0 = classic, 1 = duo, 2 = test (copie exacte du banc de test)
uniform float u_bg_frame;      // palette test: numero d'image (30 par seconde, comme SyntheticVideoStream)
uniform vec3  u_bg_c1;
uniform vec3  u_bg_c2;
uniform vec2  u_bg_phase;      // x = defilement cumule, y = nombre de bascules du damier
uniform float u_bg_tile;       // cote d'un carreau en pixels pour 720 px de haut
uniform float u_bg_checker;    // contraste du damier
uniform float u_bg_react;      // reaction au kick (flash)
uniform float u_bg_hue;        // rotation de teinte, 0..1
uniform float u_bg_angle;      // direction du degrade duo, en degres
uniform float u_beat;          // enveloppe du kick 1 -> 0
// Palette stochastique (u_bg_palette = 3) : points ronds dont la densite dessine un champ qui bouge (halftone stochastique).
uniform vec4  u_sto;           // x = taille des points (px pour 720 px de haut), y = regularite, z = variation de taille, w = densite
uniform vec4  u_sto2;          // x = champ (0 bruit, 1 vagues, 2 degrade), y = inversion, z = arc-en-ciel, w = phase du scintillement
uniform vec4  u_sto_au;        // x = basses, y = kick, z = nombre de kicks (deja multiplies par la reaction)
// Palette labyrinthe (u_bg_palette = 4) : etat d'une reaction-diffusion (r = U, g = V, grille en boucle, origine BAS gauche) lu trois
// fois avec un petit decalage chacune : trois couches colorees decalees, posees dans l'ordre sur le fond.
uniform sampler2D u_rd_tex;
uniform vec4  u_rd;            // x = ecart des couches (fraction de la hauteur), y = demi-texel de la simulation (en hauteur)
uniform vec3  u_rd_c1;
uniform vec3  u_rd_c2;
uniform vec3  u_rd_c3;
uniform vec3  u_rd_ground;

// Fonte acide du logo (dissolution sur place) : un champ de bruit local au logo est compare a un seuil
// qui monte avec u_melt ; ce qui est sous le seuil disparait, et la lisiere ronge brille d'un acide.
uniform float u_melt;          // 0 = intact .. 1 = dissous (0 = effet coupe)
uniform float u_melt_scale;    // finesse des trous
uniform float u_melt_edge;     // largeur et intensite de la lisiere acide
uniform vec3  u_melt_color;
uniform float u_melt_t;        // temps (derive lente du bruit)

// Cellules organiques du logo (Voronoi + metaballes, cell shading) : le logo se decoupe en gouttes qui
// derivent, fusionnent et se detachent en bulles, chacune rendue en aplat avec cloisons et reflet.
uniform float u_cell;          // 0 = coupe / logo intact .. 1 = entierement decompose en cellules
uniform float u_cell_scale;    // finesse des cellules
uniform float u_cell_fusion;   // rayon des cellules: petit = bulles isolees, grand = elles se touchent
uniform float u_cell_flat;     // 0 = couleurs d'origine, 1 = aplat de la couleur au centre de la cellule
uniform vec3  u_cell_ink;      // couleur du contour des cellules
uniform float u_cell_t;        // temps integre cote CPU (derive des graines)

// Mosaique de doodles : le logo (sa forme, son alpha) est reconstruit avec les medias recus du public. Un atlas de 8 x 8 vignettes
// (rgba premultiplie), une vignette par case de la grille posee sur le logo ; chaque case en change de temps en temps, a son rythme.
uniform sampler2D u_mosaic_tex;
uniform float u_mosaic;        // 0 = coupe / logo intact .. 1 = entierement en vignettes
uniform float u_mosaic_n;      // nombre de vignettes valides dans l'atlas (0 = rien a montrer)
uniform float u_mosaic_scale;  // finesse de la grille
uniform float u_mosaic_t;      // temps integre cote CPU (renouvellement des vignettes)

in vec2 v_uv;
out vec4 f_color;

vec3 hue_rotate(vec3 c, float h) {
    // Rotation autour de l'axe du gris (formule de Rodrigues).
    float a = h * 6.2831853;
    vec3 k = vec3(0.57735027);
    return c * cos(a) + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - cos(a));
}

// Palette "test": reproduit OCTET POUR OCTET SyntheticVideoStream._frame (audio2wave_gl.py) pour l'image
// numero u_bg_frame : R = gx + 3n, V = gy + 2n (entiers sur 8 bits, avec bouclage), B = 255 - gx, plus 60 sur
// un damier de 80 px (fixe, quelle que soit la resolution) qui bascule toutes les 15 images. Aucun reglage
// (teinte, taille, contraste, reaction) ne s'y applique.
vec3 test_pattern(vec2 p) {
    vec2 px = floor(p * u_res);
    // Le banc de test calcule x*255 en uint16 : ca deborde (bouclage a 65536) des x > 257, ce qui coupe le
    // degrade en bandes. Ce defaut fait partie du motif d'origine et est reproduit volontairement.
    float gx = floor(mod(px.x * 255.0, 65536.0) / max(u_res.x - 1.0, 1.0));
    float gy = floor(mod(px.y * 255.0, 65536.0) / max(u_res.y - 1.0, 1.0));
    float n = u_bg_frame;
    float r = mod(gx + mod(n * 3.0, 256.0), 256.0);
    float g = mod(gy + mod(n * 2.0, 256.0), 256.0);
    float b = 255.0 - gx;
    float check = mod(floor(px.x / 80.0) + floor(px.y / 80.0) + floor(n / 15.0), 2.0) * 60.0;
    return min(vec3(r, g, b) + check, 255.0) / 255.0;
}

// Hachage sans trigonometrie (meme formule que chash, plus bas : la palette stochastique en fait ~45 par pixel).
float sto_hash(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float sto_noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(sto_hash(i), sto_hash(i + vec2(1.0, 0.0)), f.x),
               mix(sto_hash(i + vec2(0.0, 1.0)), sto_hash(i + vec2(1.0, 1.0)), f.x), f.y);
}

// Champ de densite 0..1 en q (0..1, origine haut gauche), corrige du ratio de l'ecran ; derive avec u_bg_phase.x.
float sto_field(vec2 q) {
    float ar = u_res.x / max(u_res.y, 1.0);
    vec2 v = vec2(q.x * ar, q.y);
    float ph = u_bg_phase.x;
    float a = radians(u_bg_angle);
    vec2 dir = vec2(cos(a), sin(a));
    float f;
    if (u_sto2.x < 0.5) {                                   // bruit fluide : grandes taches qui derivent
        vec2 drift = vec2(ph * 0.11, ph * 0.07);
        f = 0.62 * sto_noise(v * 3.0 + drift) + 0.38 * sto_noise(v * 6.1 - drift * 1.3 + 11.0);
        f = smoothstep(0.18, 0.82, f);
    } else if (u_sto2.x < 1.5) {                            // vagues : bandes qui se propagent vers u_bg_angle
        float d = dot(v - vec2(ar * 0.5, 0.5), dir);
        f = 0.5 + 0.5 * sin(6.2831853 * (d * 3.0 - ph * 0.25 - u_sto_au.z * 0.25));     // chaque kick : un quart d'onde
    } else {                                                // degrade aller-retour (sans couture), comme la palette duo
        float t = dot(q - 0.5, dir) + 0.5 + ph * 0.1;
        f = abs(fract(t) * 2.0 - 1.0);
    }
    f = 0.5 + (f - 0.5) * (1.0 + 0.8 * u_sto_au.y);         // kick : coup de contraste
    return clamp(f + (u_sto.w + 0.25 * u_sto_au.x - 0.5) * 2.0, 0.0, 1.0);    // basses : densite en plus
}

// Grille jitteree : une cellule = un point, allume si le champ depasse son rang. Rang a faible discrepance (suite R2) = points
// repartis sans amas (bruit bleu) ; rang blanc = grain aleatoire ; la regularite melange les deux. 3 x 3 cellules voisines.
vec3 stochastic_pattern(vec2 p) {
    float s = max(u_sto.x * u_res.y / 720.0, 2.0);
    float grow = 1.0 + 0.3 * u_sto_au.x;                   // basses : points plus gros (la grille ne bouge pas)
    vec2 px = p * u_res;
    vec2 home = floor(px / s);
    float reg = clamp(u_sto.y, 0.0, 1.0);
    float jamp = mix(0.5, 0.3, reg);                       // decalage maximal du point dans sa cellule (en cellules)
    // Rayon plafonne a ce que le voisinage 3 x 3 peut montrer : au-dela un gros point serait coupe a plat au bord du voisinage.
    float rmax = (1.5 - jamp) * s - 1.0;
    float reach = min(0.5 * s * grow * (1.0 + abs(u_sto.z)), rmax) + 1.0;     // rayon maximal (bord anti-crenele compris)
    float cover = 0.0;
    float fdot = 0.0;
    for (int j = -1; j <= 1; j++) {
        for (int i = -1; i <= 1; i++) {
            vec2 c = home + vec2(float(i), float(j));
            vec2 jit = jamp * (vec2(sto_hash(c + 1.7), sto_hash(c + 4.1)) * 2.0 - 1.0);
            vec2 ctr = (c + 0.5 + jit) * s;
            if (length(px - ctr) > reach) {                // ce point ne peut pas atteindre le pixel : pas de champ a calculer
                continue;
            }
            float r2 = fract(c.x * 0.7548776662 + c.y * 0.5698402910);
            float wh = sto_hash(c);
            float epoch = floor(u_sto2.w + sto_hash(c + 7.3));    // aigus : chaque point change de rang a son tour
            // r2 uniforme + decalage independant, modulo 1 = toujours uniforme : la densite ne depend ni de la regularite ni du
            // scintillement (un melange mix(wh, r2, reg) donnait une loi triangulaire, densite fausse entre 0 et 1).
            float rank = fract(r2 + (1.0 - reg) * wh + epoch * 0.6180340);
            float f = sto_field(ctr / u_res);
            if (f <= 0.0 || f < rank) {
                continue;
            }
            float rad = min(0.5 * s * grow * (1.0 + u_sto.z * (f - 0.5) * 2.0), rmax);
            float k = 1.0 - smoothstep(rad - 1.0, rad + 1.0, length(px - ctr));
            if (k > cover) {
                cover = k;
                fdot = f;
            }
        }
    }
    vec3 dots = u_sto2.y > 0.5 ? u_bg_c2 : u_bg_c1;
    vec3 ground = u_sto2.y > 0.5 ? u_bg_c1 : u_bg_c2;
    vec3 col = mix(ground, hue_rotate(dots, fdot * u_sto2.z), cover);
    return clamp(hue_rotate(clamp(col, 0.0, 1.0), u_bg_hue), 0.0, 1.0);
}

float rd_layer(vec2 p, float angle) {
    float ar = u_res.x / max(u_res.y, 1.0);
    vec2 q = p + u_rd.x * vec2(cos(angle) / ar, sin(angle));     // decalage isotrope a l'ecran
    float v = texture(u_rd_tex, vec2(q.x, 1.0 - q.y)).g;
    float w = max(u_rd.y * 4.0, 0.02);                            // bord net sans crenelage (V varie vite pres du seuil)
    return smoothstep(0.25 - w, 0.25 + w, v);
}

vec3 labyrinth_pattern(vec2 p) {
    vec3 col = u_rd_ground;
    col = mix(col, u_rd_c1, rd_layer(p, 1.5707963));
    col = mix(col, u_rd_c2, rd_layer(p, 3.6651914));
    col = mix(col, u_rd_c3, rd_layer(p, 5.7595865));
    return clamp(hue_rotate(clamp(col, 0.0, 1.0), u_bg_hue), 0.0, 1.0);
}

// p : position ecran, origine HAUT gauche.
vec3 background_pattern(vec2 p) {
    if (u_bg_palette > 3.5) {
        return labyrinth_pattern(p);
    }
    if (u_bg_palette > 2.5) {
        return stochastic_pattern(p);
    }
    if (u_bg_palette > 1.5) {
        return test_pattern(p);
    }
    float tile = max(u_bg_tile * u_res.y / 720.0, 2.0);
    vec2 cell = floor(p * u_res / tile);
    float parity = mod(cell.x + cell.y + floor(u_bg_phase.y), 2.0);
    vec3 base;
    if (u_bg_palette < 0.5) {
        base = vec3(fract(p.x + u_bg_phase.x * 0.353),
                    fract(p.y + u_bg_phase.x * 0.235),
                    1.0 - p.x);
    } else {
        float a = radians(u_bg_angle);
        float t = dot(p - 0.5, vec2(cos(a), sin(a))) + 0.5 + u_bg_phase.x * 0.1;
        float tri = abs(fract(t) * 2.0 - 1.0);            // aller-retour: pas de couture
        base = mix(u_bg_c1, u_bg_c2, smoothstep(0.0, 1.0, tri));
    }
    base += parity * u_bg_checker;
    base = hue_rotate(clamp(base, 0.0, 1.0), u_bg_hue);
    base *= 1.0 + u_bg_react * 0.6 * u_beat;
    return clamp(base, 0.0, 1.0);
}

float mhash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float mnoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mhash(i), mhash(i + vec2(1.0, 0.0)), f.x),
               mix(mhash(i + vec2(0.0, 1.0)), mhash(i + vec2(1.0, 1.0)), f.x), f.y);
}

// Champ de dissolution 0..1 dans le repere du logo (q: 0..1 sur le rectangle du logo), isotrope a l'ecran.
float melt_field(vec2 q) {
    float ar = (u_logo_rect.z * u_res.x) / max(u_logo_rect.w * u_res.y, 1.0);
    vec2 v = q * vec2(ar, 1.0) * (9.0 * u_melt_scale);
    vec2 drift = vec2(0.0, u_melt_t * 0.04);
    float n = 0.42 * mnoise(v + drift) + 0.34 * mnoise(v * 2.3 + 17.0 - drift * 0.7) + 0.24 * mnoise(v * 5.3 + 3.1);
    return smoothstep(0.25, 0.75, n);
}

// Hachage sans trigonometrie (appele ~9 fois par prise, et logo_at est appelee ~13 fois par pixel avec le glow).
float chash(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

// Trois valeurs d'un coup (un seul hachage au lieu de quatre par cellule candidate).
vec3 chash3(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
    p3 += dot(p3, p3.yxz + 33.33);
    return fract((p3.xxy + p3.yzz) * p3.zyx);
}

float ctri(float x) {
    return abs(fract(x) * 2.0 - 1.0);       // onde triangulaire 0..1..0, bon marche
}

// Le logo `c` (premultiplie) se DECOMPOSE en cellules : la grille de Voronoi decoupe le logo en morceaux ; chaque
// morceau est un disque (une cellule) qui contient la partie du logo qui lui correspond et qui s'en ecarte, se
// retrecit et se balade (u_cell = degre de decomposition). Les pixels hors du logo restent vides : les cellules
// ne reprennent que la forme du logo, jamais la zone qu'il occupe.
vec4 logo_cells(vec2 q, vec4 c) {
    float ar = (u_logo_rect.z * u_res.x) / max(u_logo_rect.w * u_res.y, 1.0);
    vec2 sc = vec2(ar, 1.0) * (12.0 * u_cell_scale);
    vec2 v = q * sc;
    vec2 ci = floor(v);
    vec2 mid = 0.5 * sc;                                           // centre du logo, dans le repere des cellules
    float D = u_cell;
    float Rb = 0.75 * min(max(u_cell_fusion, 0.2), 1.2) * (1.0 - 0.25 * D);   // rayon d'une cellule (en cases)
    float best = 1.0;
    vec2 best_seed = v;
    vec2 best_ctr = v;
    for (int j = -1; j <= 1; j++) {
        for (int i = -1; i <= 1; i++) {
            vec2 id = ci + vec2(float(i), float(j));
            vec3 hh = chash3(id);
            vec2 s = id + 0.5 + 0.68 * (hh.xy - 0.5);                    // graine au repos (grille jitteree)
            float h1 = hh.z;
            float h2 = fract(h1 * 7.31 + hh.x * 3.77 + hh.y * 1.93);
            vec2 wob = vec2(ctri(u_cell_t * (0.5 + h1) + h2), ctri(u_cell_t * (0.45 + h2) + h1)) * 2.0 - 1.0;
            vec2 out_dir = (s - mid) / max(length(s - mid), 0.001);
            vec2 ctr = s + D * (0.26 * wob + 0.10 * out_dir);            // la cellule glisse et s'ecarte du centre
            float r = length(v - ctr) / (Rb * (0.8 + 0.4 * h1));         // rayons un peu inegaux
            if (r < best) {
                best = r;
                best_seed = s;
                best_ctr = ctr;
            }
        }
    }
    if (best >= 1.0) {
        return vec4(0.0);
    }
    vec2 qs = (best_seed + (v - best_ctr)) / sc;                          // ou, dans le logo, se trouve ce morceau
    float in_logo = step(0.0, qs.x) * step(qs.x, 1.0) * step(0.0, qs.y) * step(qs.y, 1.0);
    // textureLod et non texture(): ici le flot de controle diverge (return anticipe), les derivatives de 	exture seraient fausses
    // et choisiraient un mauvais niveau de mipmap aux frontieres des cellules (traits parasites).
    vec4 piece = textureLod(u_logo, clamp(qs, 0.0, 1.0), 0.5);
    piece.rgb *= mix(1.0, piece.a, u_logo_straight);
    piece *= in_logo;
    float mask = 1.0 - smoothstep(0.90, 1.0, best);
    // Couleur en aplat: moyenne (floue) du logo autour du morceau, melangee a la couleur d'origine.
    vec4 cc = textureLod(u_logo, clamp(best_seed / sc, 0.0, 1.0), 3.0);
    cc.rgb *= mix(1.0, cc.a, u_logo_straight);
    vec3 own_col = piece.a > 0.02 ? piece.rgb / piece.a : vec3(1.0);
    vec3 flat_col = cc.a > 0.004 ? cc.rgb / cc.a : own_col;
    vec3 col = mix(own_col, flat_col, u_cell_flat);
    float tone = best < 0.62 ? 1.0 : 0.8;                                     // deux niveaux: cell shading
    float hl = smoothstep(0.20, 0.12, length((v - best_ctr) / max(Rb, 0.05) - vec2(-0.38, -0.42)));   // reflet de bulle
    float ink = smoothstep(0.70, 0.95, best);                                 // contour de la cellule
    vec3 rgb = mix(col * tone + vec3(0.5) * hl, u_cell_ink, ink * 0.85);
    float a = mask * piece.a * (0.86 + 0.14 * ink);
    return mix(c, vec4(rgb * a, a), D);
}

// Le logo `c` (premultiplie) garde sa forme mais se remplit de vignettes. Les parties transparentes d'une vignette et le joint entre
// deux vignettes laissent voir la couleur d'origine du logo. textureLod : jamais de derivees dans un bloc conditionnel.
vec4 logo_mosaic(vec2 q, vec4 c) {
    float ar = (u_logo_rect.z * u_res.x) / max(u_logo_rect.w * u_res.y, 1.0);
    vec2 v = q * vec2(ar, 1.0) * (10.0 * u_mosaic_scale);
    vec2 id = floor(v);
    vec2 f = fract(v);
    vec3 hh = chash3(id);
    float era = floor(u_mosaic_t * 0.25 + hh.y * 8.0);                     // chaque case change de vignette toutes les ~4 s, decalee des autres
    float pick = chash3(id + vec2(era * 1.7, era * 3.1)).x;
    float idx = min(floor(pick * u_mosaic_n), u_mosaic_n - 1.0);
    vec2 tile = vec2(mod(idx, 8.0), floor(idx / 8.0));
    vec2 g = (f - 0.5) / 0.94 + 0.5;                                        // 3 % de joint de chaque cote
    float on = step(0.0, g.x) * step(g.x, 1.0) * step(0.0, g.y) * step(g.y, 1.0);
    vec4 m = textureLod(u_mosaic_tex, (tile + clamp(g, 0.0, 1.0) * 0.98 + 0.01) / 8.0, 0.0) * on;
    vec3 rgb = m.rgb * c.a + c.rgb * (1.0 - m.a);
    return vec4(mix(c.rgb, rgb, u_mosaic), c.a);
}

// p : position ecran, origine HAUT gauche. Renvoie le logo premultiplie (0 hors du rectangle).
vec4 logo_at(vec2 p) {
    vec2 d = p - u_logo_rect.xy;
    if (u_logo_cs.y != 0.0) {
        vec2 px = d * u_res;                                                 // la rotation se fait en pixels (pas d'etirement)
        d = vec2(u_logo_cs.x * px.x + u_logo_cs.y * px.y, -u_logo_cs.y * px.x + u_logo_cs.x * px.y) / u_res;
    }
    vec2 q = d / (2.0 * u_logo_rect.zw) + 0.5;
    float inside = step(0.0, q.x) * step(q.x, 1.0) * step(0.0, q.y) * step(q.y, 1.0);
    float n = 1.0;
    if (u_melt > 0.001 && inside > 0.5) {
        n = melt_field(q);
        q.y -= u_melt * 0.025 * (1.0 - n);                 // la matiere qui cede s'affaisse un peu
    }
    // Echantillonnage toujours execute (derivees valides pour les mipmaps), masque apres coup.
    vec4 c = texture(u_logo, clamp(q, 0.0, 1.0));
    c.rgb *= mix(1.0, c.a, u_logo_straight);
    if (u_cell > 0.001 && inside > 0.5) {
        c = logo_cells(q, c);
    }
    if (u_mosaic > 0.001 && u_mosaic_n > 0.5 && inside > 0.5) {
        c = logo_mosaic(q, c);
    }
    if (u_melt > 0.001 && inside > 0.5) {
        float w = 0.07 + 0.22 * u_melt_edge;
        float m = u_melt * (1.0 + w + 0.03);
        float vis = smoothstep(m - 0.015, m + 0.015, n);                // matiere restante
        float band = 1.0 - smoothstep(m, m + w, n);                     // zone attaquee par l'acide
        float core = 1.0 - smoothstep(m, m + w * 0.30, n);              // coeur chaud de la lisiere
        vec3 acid = mix(u_melt_color, vec3(0.82, 1.0, 0.45), core * 0.6);
        float a0 = c.a;
        c.rgb *= vis;
        c.a *= vis;
        // La matiere pres des trous VIRE a l'acide (on remplace la couleur, sinon un logo blanc + additif
        // ne donne qu'un jaune pale), avec un liseré lumineux par-dessus. Jamais hors du logo.
        float tint = band * clamp(0.9 * u_melt_edge, 0.0, 1.0);
        c.rgb = mix(c.rgb, acid * c.a, tint);
        c.rgb += acid * band * vis * a0 * u_melt_edge * 0.35;
    }
    return c * inside;
}

void main() {
    // Rawvideo arrive haut en premier : on passe en origine HAUT gauche pour lire les textures.
    vec2 p = vec2(v_uv.x, 1.0 - v_uv.y);

    if (u_pass > 0.5) {
        // Couche LOGO seule: rgb premultiplie + contour lumineux ajoute (alpha = celui du logo), pour
        // que le post-traitement la deforme independamment du fond puis la pose par-dessus.
        vec4 lg = logo_at(p) * u_logo_opacity;   // premultiplie: on pondere les 4 canaux
        vec3 rgb = lg.rgb;

        // Contour lumineux : moyenne de l'alpha sur deux anneaux autour du pixel, gardee hors du logo.
        if (u_glow > 0.001) {
            float radius = mix(0.006, 0.03, u_bass) * u_glow_radius;
            vec2 asp = vec2(u_res.y / u_res.x, 1.0);
            float acc = 0.0;
            // Hors du rectangle du logo agrandi du rayon du contour, tous les echantillons seraient vides : on les saute
            // (la plupart des pixels de l'ecran, ~25 appels de logo_at chacun, cellules et fonte comprises).
            vec2 half_ext = u_logo_rect.zw;
            if (u_logo_cs.y != 0.0) {                                       // pivote : le carre englobant devient le cercle de la diagonale
                float diag = length(u_logo_rect.zw * u_res);
                half_ext = vec2(diag / u_res.x, diag / u_res.y);
            }
            vec2 reach = vec2(half_ext.x + radius * asp.x, half_ext.y + radius);
            vec2 away = abs(p - u_logo_rect.xy);
            if (away.x < reach.x && away.y < reach.y) {
                for (int i = 0; i < 12; i++) {
                    float ang = float(i) * 0.5235988;   // 2*pi/12
                    vec2 dir = vec2(cos(ang), sin(ang)) * asp;
                    acc += logo_at(p + dir * radius).a;
                    acc += logo_at(p + dir * radius * 0.5).a;
                }
            }
            float halo = clamp(acc / 24.0 * 1.6, 0.0, 1.0) * (1.0 - lg.a);
            rgb += u_glow_color * halo * u_glow * (0.25 + 0.75 * u_bass);
        }
        f_color = vec4(clamp(rgb, 0.0, 1.0), lg.a);
        return;
    }

    // Couche FOND: video ffmpeg ou motif genere.
    vec3 col = (u_bg_mode > 0.5) ? background_pattern(p) : texture(u_video, p).rgb;
    f_color = vec4(clamp(col, 0.0, 1.0), 1.0);
}
