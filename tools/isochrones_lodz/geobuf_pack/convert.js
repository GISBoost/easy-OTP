// Converts every per-origin *.geojson in ../data/<city>/<variant>/ into a
// geobuf *.pbf binary of the same basename, then deletes the source .geojson.
// Measured on this dataset: geobuf is ~18% of raw GeoJSON size and, after
// gzip (which GitHub Pages applies automatically in transit), still ~47% of
// gzipped-GeoJSON size -- see tools/isochrones_lodz/README.md decision log.
//
// Usage: node convert.js <city> <variant: static|rt>
const fs = require("fs");
const path = require("path");
const geobuf = require("geobuf");
const Pbf = require("pbf");

const city = process.argv[2];
const variant = process.argv[3];
if (!city || !["static", "rt"].includes(variant)) {
  console.error("Usage: node convert.js <city> <variant: static|rt>");
  process.exit(1);
}

const dir = path.join(__dirname, "..", "data", city, variant);
const files = fs.readdirSync(dir).filter((f) => f.endsWith(".geojson"));

let totalOut = 0;
for (const f of files) {
  const srcPath = path.join(dir, f);
  const json = JSON.parse(fs.readFileSync(srcPath, "utf8"));
  const pbf = geobuf.encode(json, new Pbf.PbfWriter());
  const buf = Buffer.from(pbf);
  const destPath = srcPath.replace(/\.geojson$/, ".pbf");
  fs.writeFileSync(destPath, buf);
  fs.unlinkSync(srcPath);
  totalOut += buf.length;
}

console.log(`${city}/${variant}: converted ${files.length} files, total .pbf size ${(totalOut / 1e6).toFixed(1)} MB`);
