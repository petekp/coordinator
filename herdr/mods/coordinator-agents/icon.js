import { svg, image } from "gpui-kit";

// The agent's icon from the app's program table: a one-color glyph or a picture.
// oneColor draws an SVG picture as a glyph in `color` too.
export function icon(p, size, color, oneColor = false) {
  if (!p?.icon) return null;
  const glyph = p.tint || (oneColor && p.icon.endsWith(".svg"));
  return glyph ? svg(p.icon).size(size).flex_none().text_color(color) : image(p.icon).w(size).h(size).flex_none();
}
