import { svg, image } from "gpui-kit";

// The agent's icon from the app's program table: a one-color glyph when `tint`
// is set, otherwise its picture.
export function icon(p, size, color) {
  if (!p?.icon) return null;
  return p.tint ? svg(p.icon).size(size).flex_none().text_color(color) : image(p.icon).w(size).h(size).flex_none();
}
