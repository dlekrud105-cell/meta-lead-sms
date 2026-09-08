# ASIDE 통합 예제

`accounting/` 엔진을 앱 정산탭에 붙이기 위한 참조 구현입니다.
전체 맥락은 [`../docs/HANDOVER.md`](../docs/HANDOVER.md)를 먼저 보세요.

| 파일 | 내용 |
|---|---|
| `schema.sql` | PostgreSQL 14+ 스키마 (프로덕션) |
| `sql_store.py` | `store.Table`의 SQL 구현 — **이거 하나면 DB 전환 끝** |
| `api.py` | 정산탭용 HTTP 핸들러 + FastAPI 예제 |
| `test_sql_backend.py` | 엔진 테스트 148개를 SQL로 실행 |
| `test_api.py` | API 핸들러 테스트 16개 |

---

## 먼저 돌려보세요

```bash
python3 examples/test_sql_backend.py   # 148 tests, SQLite 백엔드
python3 examples/test_api.py           # 16 tests, API + SQL
```

**둘 다 통과합니다.** CSV를 SQL로 바꿔도 엔진이 그대로 돈다는 걸
말로 하는 대신 실행으로 증명한 겁니다. 서버 없이 SQLite로 돌아가니
CI에 그대로 넣으세요.

---

## DB 전환

```python
import psycopg
from examples.sql_store import bind

connection = psycopg.connect(DSN)
bind(connection, company_id=1)     # 앱 시작 시 1회

# 이 다음부터는 accounting 패키지를 평소대로 쓰면 됩니다
from accounting import reports as rp
rp.cash_position('2026-09-08')
```

`bind()`가 `store.JOURNAL` 등 8개 테이블과 `amortise.SCHEDULES`,
`store.read_company` / `write_company`를 SQL 구현으로 교체합니다.

### 스키마 적용

```bash
psql -d yourapp -f examples/schema.sql
```

그다음 계정과목을 시드하세요 (현재 `accounts.py`의 코드 상수입니다):

```python
from accounting import accounts as coa
cursor.executemany(
    'INSERT INTO accounting.account (company_id, code, name, type, tax_code, '
    'role, contra, deductible, tpar, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
    [(1, a.code, a.name, a.type, a.tax_code, a.role,
      a.contra, a.deductible, a.tpar, a.note) for a in coa.CHART])
```

### 스키마가 강제하는 것

| | |
|---|---|
| 금액 | `DECIMAL(12,2)`. **`FLOAT` 쓰면 시산표가 안 맞습니다** |
| 분개 | `UPDATE`/`DELETE` 트리거로 차단. 정정은 반대분개로만 |
| 차대일치 | `journal_entry_balance`에 deferred CHECK — COMMIT 시점 검증 |
| 중복 임포트 | `bank_line.fingerprint` PK |
| 채번 | `next_id()` 함수 — CSV의 high-water mark 방식은 동시성에 취약 |

`document_outstanding` / `trial_balance` 뷰는 BI 도구용입니다.
엔진은 이 뷰를 쓰지 않고 직접 계산합니다.

---

## API

핸들러는 **프레임워크 무관한 순수 함수**입니다. 프리미티브를 받고 JSON-safe dict를 돌려줍니다.
FastAPI 배선은 `build_app()`에 예시로만 들어있으니 Django든 뭐든 갈아끼우세요.

```python
from examples import api

api.get_dashboard(as_at)                       # 정산탭 상단 전체
api.get_report('bas', period='2026Q4')         # pl · bas · tpar · cashflow · ...
api.get_lodgement_pack('bas', period='2026Q4') # ATO 폼 입력값
api.preview_statement(pdf_path)                # 명세서 파싱 + 분류 제안
api.commit_statement(pdf_path, decisions)      # 선택한 것만 기록
api.create_invoice(payload)
api.record_lodged(payload)
```

### 두 가지만 지켜주세요

**1. Decimal은 문자열로 나갑니다**

`jsonable()`이 금액을 `"1234.50"`으로 렌더합니다. 클라이언트에서 **float로 파싱하지 마세요.**
`0.1 + 0.2`가 시산표를 깨뜨리는데, 그 사실은 한 분기 뒤 BAS에서 드러납니다.

**2. 엔진 예외 메시지를 그대로 쓰세요**

```python
ApiError('Kim Painting has not quoted an ABN but this bill claims 100.00 of GST...', 422)
```

엔진이 이미 사람이 읽을 문장으로 씁니다. `"Bad Request"`로 덮지 마세요.
`guard()`가 `TransactionError`/`LedgerError`/`ValueError` → 422,
`KeyError`/`LookupError` → 404로 매핑합니다.

### 명세서 업로드 흐름

```
POST /accounting/statements/preview  (multipart)
  → { statement: {..., reconciled: true},
      lines: [{ fingerprint, description, amount, account, status, note }],
      summary: { ready: 91, review: 59 } }

  status 가 'review' / 'unmatched' 인 줄이 확인 필요 큐입니다.
  사용자가 계정을 고르면:

POST /accounting/statements/commit
  { decisions: { "<fingerprint>": true,
                 "<fingerprint>": { account: "5100", tax_code: "GST" } } }
```

**중복 방지는 자동입니다.** 같은 명세서를 다시 올려도 `status: 'imported'`로 표시되고
다시 기록되지 않습니다.

**명세서가 대차합계와 안 맞으면 파싱 자체가 실패합니다** (`StatementError` → 422).
조용히 틀린 데이터가 들어가는 것보다 낫다는 판단입니다.

---

## 아직 안 되어 있는 것

- **멀티테넌시** — 스키마에 `company_id`는 있지만 엔진은 단일 회사 전제입니다.
  `bind(conn, company_id)`가 요청마다 전역을 바꾸므로, 멀티테넌트로 가려면
  `store` 전역 대신 컨텍스트 객체로 리팩터링이 필요합니다.
- **계정과목 커스터마이즈** — `accounts.CHART`가 코드 상수입니다. 회사별 계정을 넣으려면
  `account` 테이블에서 읽도록 바꿔야 합니다.
- **연결 풀링** — `bind()`는 커넥션 하나를 잡습니다. 프로덕션에서는 풀에서
  꺼낸 커넥션을 요청 스코프로 바인딩하세요.
- **`next_sequence` 동시성** — SQL 구현은 안전하지만, `bind()` 전역 방식 때문에
  동시 요청이 서로의 커넥션을 볼 수 있습니다. 위 리팩터링과 함께 해결됩니다.
