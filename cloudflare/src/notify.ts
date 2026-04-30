export interface TelegramSecrets {
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_CHAT_ID: string;
}

export interface SendOptions {
  productUrl?: string;
  silent?: boolean;
}

export async function sendTelegram(
  secrets: TelegramSecrets,
  text: string,
  options: SendOptions = {},
): Promise<void> {
  const url = `https://api.telegram.org/bot${secrets.TELEGRAM_BOT_TOKEN}/sendMessage`;
  const payload: Record<string, unknown> = {
    chat_id: secrets.TELEGRAM_CHAT_ID,
    text,
    parse_mode: "HTML",
    disable_web_page_preview: false,
    disable_notification: options.silent ?? false,
  };
  if (options.productUrl) {
    payload.reply_markup = {
      inline_keyboard: [[{ text: "상품 페이지 열기", url: options.productUrl }]],
    };
  }
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`telegram api ${response.status}: ${body}`);
  }
}
