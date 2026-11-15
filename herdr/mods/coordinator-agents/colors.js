// Each agent's tile color, drawn from the theme. The palette has a fixed number
// of slots, so the label that keeps an agent's choice (its slot index) means the
// same slot on every theme. A new agent takes the slot fewest live agents have,
// ties going to the earlier one.
//
// The first six slots are the theme's own ansi1..ansi6 (red, green, yellow,
// blue, magenta, cyan), never its black, white or greys. The other six are hues
// in the gaps between those six: each goes to the gap whose pieces would stay
// widest, and a gap's hues are evenly spaced, each lit and saturated like the two
// theme colors around it. The bright ansi colors aren't used: in Herdr's
// graphite and paper and in Tokyo Night, each shares its normal twin's hue.

export const LABEL = "herdr.color";

export const COUNT = 12;

const ANSI = ["ansi4", "ansi3", "ansi2", "ansi5", "ansi1", "ansi6"]; // blue, yellow, green, magenta, red, cyan: warm and cool in turn

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);

function toHsl(hex) {
  const [r, g, b] = rgb(hex);
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2, d = max - min;
  if (d === 0) return [0, 0, l];
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h / 6, s, l];
}

function fromHsl(h, s, l) {
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
  const channel = (t) => {
    t = ((t % 1) + 1) % 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return "#" + [h + 1 / 3, h, h - 1 / 3].map((t) => Math.round(channel(t) * 255).toString(16).padStart(2, "0")).join("");
}

let cached = { key: null, colors: null };

// The palette for theme `t`, COUNT colors as #rrggbb.
export function palette(t) {
  const base = ANSI.map((name) => (t[name] ?? t.accent).slice(0, 7));
  const key = base.join();
  if (cached.key === key) return cached.colors;
  const anchors = base.map((hex) => ({ hex, hsl: toHsl(hex) })).sort((a, b) => a.hsl[0] - b.hsl[0]);
  const last = anchors.length - 1;
  const gaps = anchors.map((a, i) => (i < last ? anchors[i + 1].hsl[0] - a.hsl[0] : 1 - (a.hsl[0] - anchors[0].hsl[0])));
  // Hand each extra slot to the gap whose pieces would stay widest.
  const fills = anchors.map(() => 0);
  for (let n = base.length; n < COUNT; n++) {
    let best = 0;
    for (let i = 1; i < gaps.length; i++) if (gaps[i] / (fills[i] + 1) > gaps[best] / (fills[best] + 1)) best = i;
    fills[best]++;
  }
  const between = [];
  anchors.forEach((a, i) => {
    const [h0, s0, l0] = a.hsl, [, s1, l1] = anchors[(i + 1) % anchors.length].hsl;
    for (let j = 1; j <= fills[i]; j++) {
      const f = j / (fills[i] + 1);
      between.push(fromHsl(h0 + gaps[i] * f, s0 + (s1 - s0) * f, l0 + (l1 - l0) * f));
    }
  });
  // Interleave the extra hues so neighboring slots differ.
  const colors = [...base, ...[0, 3, 1, 4, 2, 5].map((i) => between[i])];
  cached = { key, colors };
  return colors;
}

// The slot a label holds, or null when it holds none.
export function indexOf(label) {
  const i = Number(label);
  return label != null && label !== "" && Number.isInteger(i) && i >= 0 && i < COUNT ? i : null;
}

// The slot fewest agents use; `used` lists the slots in use.
export function nextIndex(used) {
  const counts = Array.from({ length: COUNT }, (_, i) => used.filter((u) => u === i).length);
  return counts.indexOf(Math.min(...counts));
}
