import { parseVariants, type Variant } from "./parse";
import { sendTelegram } from "./notify";

export interface Env {
  STOCK_STATE: KVNamespace;
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_CHAT_ID: string;
  PRODUCT_URL?: string;
}

const STATE_KEY = "stock-state-v1";
const DEFAULT_PRODUCT_URL =
  "https://www.fujifilm-korea.co.kr/products/id/1330";
const USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

interface CheckResult {
  checkedAt: string;
  variants: Variant[];
  transitions: Variant[];
  alerted: boolean;
}

interface PersistedState {
  checkedAt: string;
  variants: Record<string, Variant>;
}

export default {
  async scheduled(
    _event: ScheduledController,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<void> {
    ctx.waitUntil(runCheck(env).then((result) => {
      console.log("scheduled check", JSON.stringify(result));
    }));
  },

  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/check") {
      const result = await runCheck(env);
      return jsonResponse(result);
    }

    if (url.pathname === "/state") {
      const state = await env.STOCK_STATE.get<PersistedState>(STATE_KEY, "json");
      return jsonResponse(state);
    }

    if (url.pathname === "/clear-state") {
      await env.STOCK_STATE.delete(STATE_KEY);
      return jsonResponse({ cleared: true });
    }

    if (url.pathname === "/test-telegram") {
      const scenario = url.searchParams.get("scenario") ?? "silver";
      const productUrl = env.PRODUCT_URL ?? DEFAULT_PRODUCT_URL;
      const message = sampleAlert(scenario);
      await sendTelegram(env, message, { productUrl });
      return jsonResponse({ sent: true, scenario });
    }

    return new Response(
      "fujifilm stock monitor — try /check, /state, /clear-state, /test-telegram?scenario=silver|black|both",
      { status: 200 },
    );
  },
};

async function runCheck(env: Env): Promise<CheckResult> {
  const productUrl = env.PRODUCT_URL ?? DEFAULT_PRODUCT_URL;
  const response = await fetch(productUrl, {
    headers: {
      "User-Agent": USER_AGENT,
      "Accept-Language": "ko-KR,ko;q=0.9",
      "Accept":
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    },
    cf: { cacheEverything: false },
  });
  if (!response.ok) {
    throw new Error(`fetch failed: ${response.status} ${response.statusText}`);
  }

  const html = await response.text();
  const variants = parseVariants(html);
  if (variants.length === 0) {
    throw new Error(
      "no variants found in HTML — selectors may have changed",
    );
  }

  const previous =
    (await env.STOCK_STATE.get<PersistedState>(STATE_KEY, "json")) ??
    { checkedAt: "", variants: {} };

  const transitions = variants.filter((v) => {
    const wasInStock = previous.variants[v.name]?.inStock === true;
    return v.inStock && !wasInStock;
  });

  const checkedAt = new Date().toISOString();
  let alerted = false;

  if (transitions.length > 0) {
    const message = composeAlert(transitions, variants, checkedAt);
    await sendTelegram(env, message, { productUrl });
    alerted = true;
  }

  const newState: PersistedState = {
    checkedAt,
    variants: Object.fromEntries(variants.map((v) => [v.name, v])),
  };
  await env.STOCK_STATE.put(STATE_KEY, JSON.stringify(newState));

  return { checkedAt, variants, transitions, alerted };
}

function composeAlert(
  newlyInStock: Variant[],
  all: Variant[],
  checkedAt: string,
): string {
  const head = newlyInStock.length === 1
    ? `🔥 <b>X100VI ${newlyInStock[0].short} 재고 입고!</b>`
    : `🔥 <b>X100VI ${newlyInStock.map((v) => v.short).join("/")} 재고 입고!</b>`;

  const lines = all.map(
    (v) => `${v.inStock ? "✅" : "❌"} <b>${v.short}</b> — ${v.price}`,
  );

  return [
    head,
    "지금 바로 결제하세요. 보통 5~10분 안에 다시 품절됩니다.",
    "",
    ...lines,
    "",
    `<i>감지: ${checkedAt}</i>`,
  ].join("\n");
}

function sampleAlert(scenario: string): string {
  const silverIn: Variant = { name: "X100VI Silver", short: "실버", inStock: true, price: "₩2,250,000" };
  const silverOut: Variant = { name: "X100VI Silver", short: "실버", inStock: false, price: "품절" };
  const blackIn: Variant = { name: "X100VI Black", short: "블랙", inStock: true, price: "₩2,250,000" };
  const blackOut: Variant = { name: "X100VI Black", short: "블랙", inStock: false, price: "품절" };

  let all: Variant[];
  let newly: Variant[];
  if (scenario === "black") { all = [silverOut, blackIn]; newly = [blackIn]; }
  else if (scenario === "both") { all = [silverIn, blackIn]; newly = [silverIn, blackIn]; }
  else { all = [silverIn, blackOut]; newly = [silverIn]; }

  return "🧪 <b>[샘플]</b> Cloudflare Worker에서 발송한 테스트입니다.\n\n"
    + composeAlert(newly, all, new Date().toISOString());
}

function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data, null, 2), {
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
