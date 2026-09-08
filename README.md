# meta-lead-sms

두 가지가 들어 있습니다.

1. **리드 → SMS** — Meta 리드폼 웹훅을 받아 AWS SNS로 문자를 보내는 Flask 앱
2. **회계 시스템** — 호주(NSW) 페인팅 Pty Ltd용 복식부기 장부 + BAS/TPAR 신고 집계

## 1. Meta 리드 SMS

Meta 리드 광고에서 리드가 들어오면 즉시 휴대폰으로 문자를 보냅니다.

```bash
pip install -r requirements.txt
cp .env.example .env    # 값을 채우세요
python3 app.py
```

| 엔드포인트 | 용도 |
|---|---|
| `GET /` | 헬스체크 |
| `GET /webhook` | Meta 웹훅 검증 |
| `POST /webhook` | 리드 수신 → SMS 발송 |
| `GET /debug-sns` | SNS 설정·샌드박스 상태 확인 |

## 2. 회계 시스템

디렉터 2명, GST 등록(분기 BAS), 하청업자 사용을 전제로 만들었습니다.

```bash
python3 -m accounting setup --name "Your Painting Pty Ltd" --registered 2026-01-15 \
  --director "이름1" --director "이름2" --gst-basis cash

python3 -m accounting import-bank statement.pdf --post   # 은행 명세서 자동 분류
python3 -m accounting check          # 지금 문제가 뭔지 한 번에
python3 -m accounting report bas --period 2026Q3
python3 -m accounting report tpar --fy 2026
```

**→ 전체 사용법은 [`docs/accounting-au.md`](docs/accounting-au.md)**
**→ 앱 통합/인계는 [`docs/HANDOVER.md`](docs/HANDOVER.md)**
**→ SQL 스키마·API 예제는 [`examples/`](examples/)**

담당하는 것: **CommBank 명세서 자동 입력**(은행 합계와 대조 검증) · 복식부기 원장 ·
인보이스/청구서 · cash/accruals GST · BAS 라벨 자동집계 ·
TPAR(건설업 하청 신고) · ABN 미제출 시 47% 원천징수 · 디렉터 급여/super 12%/배당 ·
Division 7A 경고 · 현장별 수익성 · 법인세 추정 · ATO·ASIC 마감 캘린더.

```bash
python3 -m unittest discover -s tests
```

장부는 `data/`에 CSV로 저장되며 기본적으로 git에서 제외됩니다.
`/accounting` 웹 대시보드는 `ACCOUNTING_TOKEN`을 설정했을 때만 열립니다(읽기 전용).
