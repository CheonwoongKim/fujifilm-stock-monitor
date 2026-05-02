import { parseVariants, type Variant } from "./parse";
import { sendTelegram } from "./notify";

export interface Env {
  STOCK_STATE: KVNamespace;
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_CHAT_ID: string;
  PRODUCT_URL?: string;
}

const STATE_KEY = "stock-state-v1";
const LOG_KEY = "stock-log-v1";
const LOG_MAX_ENTRIES = 100;
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

interface LogEntry {
  at: string;
  source: "cron" | "manual";
  silver: { inStock: boolean; price: string } | null;
  black: { inStock: boolean; price: string } | null;
  alerted: boolean;
  error?: string;
}

export default {
  async scheduled(
    event: ScheduledController,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<void> {
    ctx.waitUntil(handleScheduled(event, env));
  },

  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/check") {
      const result = await runCheck(env, "manual");
      return jsonResponse(result);
    }

    if (url.pathname === "/state") {
      const state = await env.STOCK_STATE.get<PersistedState>(STATE_KEY, "json");
      return jsonResponse(state);
    }

    if (url.pathname === "/log") {
      const log = (await env.STOCK_STATE.get<LogEntry[]>(LOG_KEY, "json")) ?? [];
      return jsonResponse({ count: log.length, entries: log.slice().reverse() });
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
      "fujifilm stock monitor — try /check, /state, /log, /clear-state, /test-telegram?scenario=silver|black|both",
      { status: 200 },
    );
  },
};

async function handleScheduled(
  event: ScheduledController,
  env: Env,
): Promise<void> {
  let result: CheckResult | null = null;
  let errorMessage: string | undefined;
  try {
    result = await runCheck(env, "cron");
  } catch (err) {
    errorMessage = err instanceof Error ? err.message : String(err);
    console.error("cron check failed", errorMessage);
  }

  const scheduledAt = new Date(event.scheduledTime);
  const isLastCronOfWindow =
    scheduledAt.getUTCHours() === 1 && scheduledAt.getUTCMinutes() === 9;

  if (isLastCronOfWindow) {
    try {
      await sendDailySummary(env, scheduledAt, result, errorMessage);
    } catch (err) {
      console.error(
        "failed to send daily summary",
        err instanceof Error ? err.message : err,
      );
    }
  }
}

async function appendLog(env: Env, entry: LogEntry): Promise<void> {
  const existing =
    (await env.STOCK_STATE.get<LogEntry[]>(LOG_KEY, "json")) ?? [];
  const next = [...existing, entry].slice(-LOG_MAX_ENTRIES);
  await env.STOCK_STATE.put(LOG_KEY, JSON.stringify(next));
}

async function sendDailySummary(
  env: Env,
  scheduledAt: Date,
  result: CheckResult | null,
  errorMessage: string | undefined,
): Promise<void> {
  const productUrl = env.PRODUCT_URL ?? DEFAULT_PRODUCT_URL;
  const dateLabel = formatKstDate(scheduledAt);

  let body: string;
  if (errorMessage) {
    body = `⚠️ <b>${dateLabel} 모니터 오류</b>\n오늘 마지막 체크에서 실패: <code>${escapeHtml(errorMessage)}</code>`;
  } else if (result === null) {
    body = `⚠️ <b>${dateLabel} 모니터 오류</b>\n원인 불명의 실패`;
  } else {
    const lines = result.variants.map(
      (v) => `${v.inStock ? "✅" : "❌"} <b>${v.short}</b> — ${v.price}`,
    );
    body =
      `💤 <b>${dateLabel} 모니터 정상 종료</b>\n` +
      `09:50–10:09 KST 폴링 완료. OUT→IN 전이 없음.\n\n` +
      lines.join("\n");
  }

  await sendTelegram(env, body, { productUrl, silent: true });
}

function formatKstDate(d: Date): string {
  const kst = new Date(d.getTime() + 9 * 60 * 60 * 1000);
  const yyyy = kst.getUTCFullYear();
  const mm = String(kst.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(kst.getUTCDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function runCheck(env: Env, source: "cron" | "manual"): Promise<CheckResult> {
  const productUrl = env.PRODUCT_URL ?? DEFAULT_PRODUCT_URL;
  const checkedAt = new Date().toISOString();
  try {
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
      throw new Error("no variants found in HTML — selectors may have changed");
    }

    const previous =
      (await env.STOCK_STATE.get<PersistedState>(STATE_KEY, "json")) ??
      { checkedAt: "", variants: {} };

    const transitions = variants.filter((v) => {
      const wasInStock = previous.variants[v.name]?.inStock === true;
      return v.inStock && !wasInStock;
    });

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

    const silver = variants.find((v) => v.short === "실버");
    const black = variants.find((v) => v.short === "블랙");
    await appendLog(env, {
      at: checkedAt,
      source,
      silver: silver ? { inStock: silver.inStock, price: silver.price } : null,
      black: black ? { inStock: black.inStock, price: black.price } : null,
      alerted,
    });

    return { checkedAt, variants, transitions, alerted };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    await appendLog(env, {
      at: checkedAt,
      source,
      silver: null,
      black: null,
      alerted: false,
      error: message,
    });
    throw err;
  }
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
