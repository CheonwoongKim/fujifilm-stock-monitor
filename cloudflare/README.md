# Fujifilm Stock Monitor — Cloudflare Worker

GitHub Actions cron의 신뢰성 문제(수 시간 지연·드롭)를 해결하기 위한 **Cloudflare Workers 기반 모니터**입니다.

| 항목 | 값 |
|---|---|
| 트리거 | Cloudflare Cron (초 단위 정확) |
| 실행 시간 | 매일 09:50–10:09 KST (1분 간격, 20회) |
| 비용 | **무료** (Workers + KV 무료 티어 사용) |
| 의존성 | wrangler CLI + Cloudflare 계정만 |

후지필름몰 상품 페이지가 **서버 사이드 렌더링**으로 `data-soldout` 속성을 직접 노출하므로 Playwright 같은 헤드리스 브라우저는 불필요합니다 (HTTP fetch + 정규식 파싱만으로 충분).

---

## 무료 티어 사용량 (하루 기준)

| 자원 | 사용 | 한도 | 비율 |
|---|---|---|---|
| Workers 호출 | 20회 | 100,000회 | 0.02% |
| KV 읽기 | 20회 | 100,000회 | 0.02% |
| KV 쓰기 | 20회 | 1,000회 | 2% |
| CPU 시간 / 호출 | ~5ms | 10ms | 50% |

---

## 1단계. wrangler CLI 설치 + 로그인

```bash
cd cloudflare
npm install            # wrangler + types 설치
npx wrangler login     # 브라우저 OAuth → Cloudflare 계정 인증
```

> Cloudflare 계정이 없으면 https://dash.cloudflare.com/sign-up 에서 무료 가입 (카드 등록 불필요).

---

## 2단계. KV 네임스페이스 생성

```bash
npx wrangler kv:namespace create stock-state
```

출력 예시:
```
🌀 Creating namespace with title "fujifilm-stock-monitor-stock-state"
✨ Success!
Add the following to your configuration file in your kv_namespaces array:
{ binding = "STOCK_STATE", id = "abc123def4567890abc123def4567890" }
```

`wrangler.toml` 의 `id = "REPLACE_AFTER_KV_CREATE"` 부분을 위에서 받은 실제 id로 교체하세요.

---

## 3단계. Secrets 등록 (Telegram)

```bash
npx wrangler secret put TELEGRAM_BOT_TOKEN
# 프롬프트에 봇 토큰 붙여넣기 + Enter

npx wrangler secret put TELEGRAM_CHAT_ID
# 프롬프트에 chat_id 붙여넣기 + Enter
```

> 시크릿은 GitHub Secrets와 별개로 Cloudflare 측에 저장됩니다. 동일한 봇 토큰/chat_id를 다시 사용하면 됩니다.

---

## 4단계. 배포

```bash
npx wrangler deploy
```

출력 끝부분에 다음과 같은 URL이 나옵니다:
```
Deployed fujifilm-stock-monitor (X.XX sec)
  https://fujifilm-stock-monitor.<your-subdomain>.workers.dev
```

---

## 5단계. 즉시 동작 검증 (수동 트리거)

브라우저로 다음 URL 방문 (또는 `curl`):

```
https://fujifilm-stock-monitor.<your-subdomain>.workers.dev/check
```

응답 예시 (현재 둘 다 품절):
```json
{
  "checkedAt": "2026-04-30T13:25:00.000Z",
  "variants": [
    { "name": "X100VI Silver", "short": "실버", "inStock": false, "price": "품절" },
    { "name": "X100VI Black",  "short": "블랙", "inStock": false, "price": "품절" }
  ],
  "transitions": [],
  "alerted": false
}
```

이 시점에서 Cloudflare KV에 첫 상태가 저장됩니다. 이후 cron 트리거에서 OUT→IN 전이가 일어나면 텔레그램 발송.

다른 디버그 라우트:
- `/state` — 현재 KV에 저장된 상태 보기
- `/clear-state` — KV 상태 초기화 (다음 첫 폴링 시 모두 `transition`으로 인식되어 알림 발송됨 — **테스트용으로만 사용**)

---

## 6단계. cron 등록 확인

```bash
npx wrangler triggers list
```

또는 Cloudflare 대시보드: Workers & Pages → fujifilm-stock-monitor → Triggers 탭 → Cron Triggers에 두 항목이 있어야 함:
- `50-59 0 * * *`
- `0-9 1 * * *`

---

## 다음날 09:50 KST 자동 동작

- 09:50, 09:51, …, 10:09 KST에 매분 Worker가 실행됨 (총 20회)
- OUT → IN 전이 감지 시 즉시 텔레그램 푸시
- 무료 티어에 충분히 들어가는 사용량

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `npx wrangler deploy` 시 `id = "REPLACE_AFTER_KV_CREATE"` 에러 | 2단계의 KV id를 `wrangler.toml` 에 반영하지 않음 |
| `/check` 응답이 `{"variants": []}` | 후지필름몰 HTML 구조 변경. `src/parse.ts` 의 정규식을 실제 DOM에 맞게 조정 |
| `/check`가 401/403 | 후지필름몰이 봇 차단. User-Agent를 브라우저 표시로 위장 (이미 적용) |
| 텔레그램 알림 안 옴 | `wrangler secret list` 로 두 secret 모두 등록됐는지 확인. cron 시간대(09:50–10:09 KST)에 실제 입고가 없었을 수도 있음 |
| 같은 상태인데 매번 알림 옴 | KV 쓰기가 실패했을 가능성. `npx wrangler tail` 로 로그 확인 |

---

## 로컬 개발 (선택)

```bash
npx wrangler dev --remote
```

→ 로컬 머신에 임시 URL이 생성됨. `/check` 라우트로 동작 확인 가능. cron은 발동 안 됨 (`--test-scheduled` 플래그로 시뮬레이션 가능).

```bash
# cron handler 시뮬레이션
curl "http://localhost:8787/__scheduled?cron=50+0+*+*+*"
```

---

## GitHub Actions와의 관계

현재 두 시스템이 **병렬 운영** 중입니다:

| 시스템 | 상태 | 역할 |
|---|---|---|
| Cloudflare Worker | **메인** | 정확한 1분 간격 폴링 |
| GitHub Actions (`drop-window.yml`) | 백업 (~1주일) | Cloudflare 신뢰성 검증되면 비활성화 |

Cloudflare가 한 주간 정상 동작 확인되면 GitHub Actions 워크플로우를 disable:
```bash
gh workflow disable drop-window.yml
```
