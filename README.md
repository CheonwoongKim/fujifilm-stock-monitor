# Fujifilm X100VI 재고 모니터

매일 **09:50~10:10 KST (20분)** 동안만 후지필름몰 X100VI 상품 페이지를 1분 간격으로 확인하고, **품절 → 입고 전이가 감지되는 순간 텔레그램으로 알림**을 보내는 GitHub Actions 봇입니다. 알림에는 상품 페이지 즉시 열기 버튼이 포함되어 있어 폰에서 곧바로 결제로 진입할 수 있습니다.

> ⚠️ 본 도구는 **재고 알림 전용**입니다. 자동 결제는 포함되지 않습니다 (쇼핑몰 약관·결제 인증 단계 우회 리스크 때문). 알림을 받은 뒤 직접 빠르게 결제하세요.

---

## 동작 방식

| 워크플로우 | 트리거 (UTC) | 트리거 (KST) | 실행 시간 | 폴링 간격 |
|---|---|---|---|---|
| `drop-window.yml` | 매일 `00:50` | 매일 `09:50` | 20분 | 1분 |

- 매일 09:50 KST에 한 번만 가동 → 20번 폴링 후 자동 종료 (10:10경)
- 상태(state.json)는 GitHub Actions 캐시로 보존, **OUT → IN 전이 시에만** 알림 (스팸 방지)
- 입고가 감지되면 폴링 중에도 즉시 텔레그램 푸시 발송

> GitHub Actions cron은 부하 시 수 분 지연될 수 있습니다. 09:50 정각이 절대 보장되지는 않으므로, 더 안전하게 하려면 cron을 `45 0 * * *` (09:45 KST)로 앞당기고 `duration_minutes`를 25~30으로 늘리면 됩니다.

---

## 1단계. 텔레그램 봇 만들기

1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 검색 → 대화 시작
2. `/newbot` 명령 → 봇 이름과 username 지정
3. 받은 **HTTP API 토큰** 복사 (예: `7891234567:ABCdef...`)
4. 만든 봇과 대화방을 열고 `/start` 또는 아무 메시지나 전송
5. 브라우저에서 다음 URL 방문 (TOKEN 부분 치환):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
6. 응답 JSON에서 `result[0].message.chat.id` 값을 복사 → **chat_id**

> 봇이 메시지를 보내려면 사용자가 먼저 봇에게 한 번이라도 말을 걸어야 합니다. 5번에서 `result`가 비어있다면 4번을 다시 하세요.

---

## 2단계. GitHub 저장소 생성 & 푸시

```bash
cd /Users/cheonwoongkim/Desktop/dev/100vi
git init -b main
git add .
git commit -m "init: fujifilm stock monitor"

# GitHub CLI 권장 (없으면 웹에서 빈 repo 만든 뒤 git remote add)
gh repo create fujifilm-stock-monitor --public --source=. --push
```

> **public repo 권장**: GitHub Actions 무료 분(分) 무제한.
> 본 설정은 하루 약 25~30분만 돌기 때문에 private repo (월 2,000분 무료)에서도 충분히 가능합니다.

---

## 3단계. Secrets 등록

저장소 페이지 → **Settings → Secrets and variables → Actions → New repository secret**

| 이름 | 값 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 1단계에서 받은 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 1단계에서 얻은 chat_id |

---

## 4단계. 첫 수동 실행 & DOM 검증

1. 저장소 → **Actions** 탭 → `stock-monitor-drop-window` 선택
2. **Run workflow** 클릭 → `debug_dump` 체크 → `duration_minutes` 1로 줄여서 실행 (테스트용)
3. 실행 완료 후 artifact `debug-<run_id>` 다운로드
4. 안에 들어있는 `page-*.html`을 열어 다음을 확인:
   - 페이지에 "품절"이 표시되는 위치
   - "구매하기"/"장바구니" 버튼 텍스트
5. 텔레그램 채널 자체 동작은 로컬에서 테스트:
   ```bash
   export TELEGRAM_BOT_TOKEN=...
   export TELEGRAM_CHAT_ID=...
   python -m pip install -r requirements.txt
   python src/notify.py
   ```
6. 필요 시 `src/check.py`의 `OUT_OF_STOCK_KEYWORDS` / `IN_STOCK_BUTTON_KEYWORDS`를 실제 DOM에 맞게 조정 후 재푸시

---

## 5단계. 가동 확인

다음날 09:50 KST 직후 Actions 탭에서 `stock-monitor-drop-window`가 자동 실행되어 약 20분 동안 돌고 ✅로 끝나는지 확인하세요.

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| 알림이 한 번도 안 옴 | 입고 자체가 없었거나, 분류 로직이 IN을 못 잡았을 가능성. `debug_dump`로 HTML 확인 후 키워드 조정 |
| `UNKNOWN` 상태가 자주 찍힘 | 페이지 로드 실패 또는 셀렉터 미스. 디버그 dump → `IN_STOCK_BUTTON_KEYWORDS`에 실제 버튼 텍스트 추가 |
| 09:50에 시작 안 함 | GitHub cron은 부하 시 수 분~십수 분 지연 가능. cron을 `45 0`로 당기고 `duration_minutes`를 25~30으로 늘려 안전 마진 확보 |
| 알림이 폭주함 | 첫날만 그럴 수 있음 (이전 상태 캐시 없음). 둘째 날부터 OUT→IN 전이 시에만 1회 |

---

## 파일 구조

```
.
├── .github/workflows/
│   └── drop-window.yml      # 09:50 KST 시작, 20분 1분 간격 폴링
├── src/
│   ├── check.py             # 페이지 확인 + 상태 비교 + 알림 트리거
│   └── notify.py            # 텔레그램 sendMessage 래퍼
├── requirements.txt
├── .gitignore
└── README.md
```

## 결제를 더 빠르게 하기 위한 팁

알림 자체는 즉시 도착하지만, **결제 단계가 진짜 병목**입니다. 미리 해두세요:

1. 후지필름몰에 로그인 상태 유지 (PC + 모바일 양쪽)
2. 배송지·결제수단을 미리 등록 (간편결제 토큰 저장)
3. 본인인증(휴대폰 PASS) 앱 미리 로그인 + 자동로그인
4. 모바일 브라우저 즐겨찾기에 상품 페이지 등록
5. 알림 도착 시 폰에서 즉시 → 카트 → 결제 (보통 30초 이내 가능해야 합니다)
