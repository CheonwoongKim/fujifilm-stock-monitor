export interface Variant {
  name: string;
  short: string;
  inStock: boolean;
  price: string;
}

const ITEM_RE =
  /<div[^>]*class="[^"]*selected-product__item[^"]*"[^>]*>[\s\S]*?<\/div>/g;
const SOLDOUT_RE = /data-soldout="(true|false)"/i;
const NAME_RE = /selected-product__name">([^<]*)</;
const PRICE_RE = /selected-product__price">([^<]*)</;

export function parseVariants(html: string): Variant[] {
  const matches = html.match(ITEM_RE) ?? [];
  return matches.map((block) => {
    const soldout = SOLDOUT_RE.exec(block)?.[1]?.toLowerCase() === "true";
    const name = (NAME_RE.exec(block)?.[1] ?? "").trim();
    const price = (PRICE_RE.exec(block)?.[1] ?? "").trim();
    return { name, short: shortLabel(name), inStock: !soldout, price };
  });
}

export function shortLabel(name: string): string {
  const lower = name.toLowerCase();
  if (lower.includes("silver")) return "실버";
  if (lower.includes("black")) return "블랙";
  return name;
}
