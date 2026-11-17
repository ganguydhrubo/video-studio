// Convert a TTF to a three.js typeface JSON (Latin + Latin-1 only). Usage: node make_typeface.cjs <fontkit dir> <in.ttf> <out.json>
const fontkit = require(process.argv[2]);
const font = fontkit.openSync(process.argv[3]);
const r = (v) => Math.round(v);
const glyphs = {};
const chars = [];
for (let c = 32; c < 127; c++) chars.push(c);
for (let c = 160; c < 256; c++) chars.push(c);
for (const c of [0x2018, 0x2019, 0x201C, 0x201D, 0x2013, 0x2014, 0x2026, 0x20B9, 0x20AC]) chars.push(c);
for (const code of chars) {
  if (!font.hasGlyphForCodePoint(code)) continue;
  const g = font.glyphForCodePoint(code);
  const o = [];
  for (const cmd of g.path.commands) {
    const a = cmd.args.map(r);
    if (cmd.command === "moveTo") o.push("m", a[0], a[1]);
    else if (cmd.command === "lineTo") o.push("l", a[0], a[1]);
    else if (cmd.command === "quadraticCurveTo") o.push("q", a[2], a[3], a[0], a[1]);
    else if (cmd.command === "bezierCurveTo") o.push("b", a[4], a[5], a[0], a[1], a[2], a[3]);
  }
  const bb = g.bbox;
  glyphs[String.fromCodePoint(code)] = { ha: r(g.advanceWidth), x_min: r(bb.minX), x_max: r(bb.maxX), o: o.join(" ") };
}
const out = {
  glyphs, familyName: font.familyName, ascender: font.ascent, descender: font.descent,
  underlinePosition: font.underlinePosition, underlineThickness: font.underlineThickness,
  boundingBox: { yMin: font.bbox.minY, xMin: font.bbox.minX, yMax: font.bbox.maxY, xMax: font.bbox.maxX },
  resolution: font.unitsPerEm, original_font_information: { format: 0, fontFamily: font.familyName, copyright: font.copyright || "", license: "SIL Open Font License 1.1" },
  cssFontWeight: "bold", cssFontStyle: "normal",
};
require("fs").writeFileSync(process.argv[4], JSON.stringify(out));
console.log(Object.keys(glyphs).length, "glyphs");
