# 인계 문서 — 회계 엔진 → 앱 정산탭

**대상:** ASIDE 팀
**목적:** `accounting/` 패키지를 회사 앱의 **정산 탭**에 통합
**저장소:** `dlekrud105-cell/meta-lead-sms` · 브랜치 `claude/painter-accounting-setup-232kdq`

---

## 0. 한 줄 요약

호주 NSW 페인팅 Pty Ltd(YOUR PAINTER SERVICE PTY LTD)용 **복식부기 회계 엔진**입니다.
표준 라이브러리만 사용하고, 은행 명세서를 읽어 장부를 만들고, ATO 신고서(BAS·TPAR·SGC·STP)에
넣을 숫자를 산출합니다. **148개 테스트 통과.**

Flask 의존성은 `web.py` 하나뿐이고 나머지는 순수 Python이라, 어떤 웹 프레임워크에도 붙습니다.

---

## 1. 설계 원칙 — 먼저 읽어주세요

### 원장이 유일한 저장소입니다

```
journal.csv (분개)  ──derive──▶  모든 리포트
```

- 잔액을 어디에도 캐시하지 않습니다. 손익·재무상태표·BAS·TPAR 전부 매번 원장에서 재계산합니다.
- **따라서 장부가 자기 자신과 어긋날 수 없습니다.**
- 성능보다 정합성을 택한 설계입니다. 거래 수천 건 규모에서는 문제없지만,
  수십만 건이면 캐시 레이어가 필요합니다 (§6 참고).

### 검증은 입력 시점에 합니다

`ledger.post()`가 거부하는 것:
- 차변 ≠ 대변
- 존재하지 않는 계정
- 금액 0 또는 음수
- 통제계정(AR/AP/GST)에 세금코드 부착

잘못된 데이터는 애초에 들어가지 않습니다. 나중에 정리할 필요가 없습니다.

### 판단이 필요한 건 자동화하지 않습니다

식사비가 접대비인지 현장 간식인지, 디렉터에게 나간 돈이 급여인지 대여금인지 —
이런 건 **`review` 플래그를 달고 사람 확인을 기다립니다.** 자동으로 기록하지 않습니다.
정산탭 UI에서 이 부분이 "확인 필요" 큐가 되어야 합니다.

---

## 2. 모듈 지도

```
                    money.py  periods.py  taxcodes.py  abn.py
                        │         │           │          │
                        └─────────┴─────┬─────┴──────────┘
                                        │
        accounts.py ────────────────────┤
        store.py    ────────────────────┤
        config.py   ────────────────────┤
                                        ▼
                                   ledger.py          ◀── 유일한 쓰기 지점
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
            transactions.py       reports.py          calendar_au.py
                    │                   │                   │
        ┌───────────┤                   ├───────────┐       │
        ▼           ▼                   ▼           ▼       ▼
  bankstatement  amortise           lodge.py     web.py   cli.py
  bankrules
  bankimport
```

| 모듈 | 줄 | 역할 |
|---|---:|---|
| `money.py` | 42 | Decimal 금액, ROUND_HALF_UP, GST 계산. **float 절대 금지** |
| `periods.py` | 136 | 호주 회계연도(7/1~6/30), BAS 분기, 자체신고/세무대리인 마감일, Pay Day Super 경계 |
| `taxcodes.py` | 73 | GST / CAP / FRE / INP / NT 와 각각의 BAS 라벨 매핑 |
| `abn.py` | 70 | ABN·ACN 체크섬 검증 (modulus 89 / 10) |
| `accounts.py` | 209 | 계정과목 65개. 페인팅업 특화. contra 계정 처리 |
| `store.py` | 142 | CSV 테이블 추상화. **여기만 바꾸면 DB로 전환 가능** |
| `config.py` | 160 | 회사 정보, 세율, 디렉터. `data/company.json` |
| `ledger.py` | 278 | **복식부기 코어.** 분개 검증·기록·조회·잔액 |
| `transactions.py` | 779 | 인보이스·청구서·지출·급여·BAS정산·배당·대여금·자산구입·할부상환 |
| `reports.py` | 1083 | 시산표·손익·재무상태표·BAS·TPAR·연령분석·현금흐름·Div7A·SGC·법인세추정 |
| `calendar_au.py` | 182 | 설립일에서 모든 ATO·ASIC 마감일 생성 |
| `bankstatement.py` | 224 | CommBank PDF 명세서 파서 |
| `bankrules.py` | 163 | 거래처명 → 계정·세금코드 매핑 규칙 |
| `bankimport.py` | 192 | 명세서 → 분개 제안·기록. 중복 방지 |
| `amortise.py` | 150 | 할부금융 상환 스케줄 (원금/이자 분리) |
| `lodge.py` | 277 | ATO 폼에 그대로 옮겨적을 신고 자료 |
| `lodgements.py` | 82 | 신고 완료 기록 (마감 알림에서 제외) |
| `jobs.py` / `contacts.py` | 228 | 현장·거래처 마스터 |
| `cli.py` | 1351 | 명령행 인터페이스 (참조 구현) |
| `web.py` | 186 | Flask 대시보드 (읽기 전용, 참조 구현) |
| `render.py` | 42 | 텍스트 테이블 (CLI 전용, 웹에선 불필요) |

---

## 3. 정산탭이 호출할 API

**`cli.py`는 참조 구현입니다. 앱은 아래 모듈 함수를 직접 호출하세요.**
CLI를 subprocess로 부르지 마세요.

### 3.1 조회 (읽기 전용 — 정산탭 메인 화면)

```python
from accounting import reports as rp

rp.cash_position(as_at)          # 통장 / 떼둘 돈 / 쓸 수 있는 돈
rp.profit_and_loss(start, end)   # 손익 (손금불산입 add-back 포함)
rp.balance_sheet(as_at)          # 재무상태표 (.balances 로 검증)
rp.bas(start, end)               # BAS 라벨 전체
rp.tpar(fy)                      # 하청 지급 신고
rp.aged_receivables(as_at)       # 미수금 연령분석
rp.aged_payables(as_at)
rp.cashflow(start, end)          # 월별 현금흐름
rp.job_results(start, end)       # 현장별 수익성
rp.director_loans(as_at)         # 디렉터 대여금
rp.division_7a_warnings(as_at)   # Div 7A 경고 (list[str])
rp.super_shortfalls(as_at)       # 연금 미적립 [(fy, SuperShortfall)]
rp.late_super(as_at)             # 기한 넘긴 연금
rp.sgc_estimate(quarter_start, shortfall, employees)
rp.tax_estimate(fy)              # 법인세 추정
rp.gst_turnover(as_at)           # 12개월 이동 매출 (등록 임계치)
```

모두 **dataclass**를 반환합니다. `dataclasses.asdict()`로 JSON 직렬화하되,
**Decimal은 문자열로 변환**하세요 (float 금지).

### 3.2 마감일

```python
from accounting import calendar_au as cal

cal.obligations(company)              # 전체
cal.overdue(today, company)           # 지난 것 (신고완료 기록분 제외)
cal.upcoming(today, within_days=120, company=...)
```

`Obligation.status(today)` → `'LODGED' | 'IN FORCE' | 'OVERDUE' | 'DUE SOON' | 'UPCOMING'`

### 3.3 기록 (쓰기)

```python
from accounting import transactions as tx

tx.create_invoice(date, contact, lines, due_days=, job=)
tx.record_receipt(date, doc_id, amount=None)      # None이면 전액
tx.create_bill(date, contact, lines)              # → result['warnings'] 확인
tx.pay_bill(date, doc_id, amount=None)
tx.spend_money(date, account, amount_incl, tax_code=, job=, bank=)
tx.receive_money(date, account, amount_incl, ...)
tx.pay_wages(date, director, gross, payg_withheld, super_amount=None)
tx.pay_super(date, amount)
tx.pay_bas(date, gst_on_sales, gst_on_purchases, payg_withholding, payg_instalment)
tx.pay_dividend(date, director, amount, franked=True)
tx.director_loan(date, director, amount, direction='to_director'|'from_director')
tx.pay_asset_deposit(date, amount)                # 인도 전 계약금
tx.buy_asset(date, asset_account, taxable_ex, gst=, gst_free=, deposit=,
             financed=, deposit_account=)         # → result['warnings'], ['notes']
tx.finance_payment(date, amount, interest, finance_account='2800')
tx.record_depreciation(date, asset_account, amount)
tx.manual_journal(date, memo, lines)
```

**모든 함수가 `TransactionError` 또는 `LedgerError`를 던집니다.** 잡아서 UI에 그대로 보여주세요 —
메시지가 사용자용으로 쓰여 있습니다.

여러 함수가 `result['warnings']`를 반환합니다 (ABN 미제출, car limit 초과 등).
**무시하지 말고 UI에 표시하세요.**

### 3.4 은행 명세서 임포트 — 정산탭의 핵심 기능

```python
from accounting import bankstatement, bankimport, bankrules

statement = bankstatement.parse_file(path)   # StatementError 가능
# statement.reconcile() 이 빈 리스트여야 정상 (parse가 이미 검증)

proposals = bankimport.propose(statement, company)
# 각 Proposal.status: 'ready' | 'review' | 'unmatched' | 'imported'

for p in proposals:
    if p.status == bankimport.READY:
        bankimport.post(p, company)
    # review / unmatched 는 사용자에게 보여주고 계정을 받으세요
    # bankimport.post(p, company, override_account='5100', override_tax_code='GST')

bankrules.add(pattern, account, tax_code=, direction=, contact=)
```

**중복 방지는 자동입니다** — 날짜+금액+방향+잔액+내용 지문(fingerprint)으로 판별합니다.
같은 명세서를 두 번 올려도 안전합니다.

### 3.5 신고 자료

```python
from accounting import lodge

lodge.bas_pack(start, end, payg_instalment=0)   # Pack(fields=[Field(label, description, value, source)])
lodge.tpar_pack(fy)                              # Pack(rows=[...], row_headers=[...])
lodge.sgc_pack(as_at)
lodge.stp_pack(fy)
```

`Pack.warnings`가 비어있지 않으면 **신고 전에 해결해야 할 문제**입니다.
`Pack.where`가 ATO 화면 경로입니다.

신고 후 기록:
```python
from accounting import lodgements
lodgements.record('BAS', 'Q4 FY2026', date, reference=, amount=, lodged_by=)
```

---

## 4. 데이터 모델

### 4.1 CSV 스키마

| 파일 | 컬럼 |
|---|---|
| `journal.csv` | entry_id, date, memo, source, doc_ref, line_no, account, description, debit, credit, tax_code, contact, job |
| `contacts.csv` | contact_id, name, type, abn, abn_quoted, gst_registered, email, phone, address, notes |
| `documents.csv` | doc_id, type, date, due_date, contact_id, job_id, description, total_incl, gst, withheld, entry_id |
| `jobs.csv` | job_id, name, contact_id, address, status, quoted_incl, started, completed, notes |
| `lodgements.csv` | kind, period, lodged_date, reference, amount, lodged_by, notes |
| `import_rules.csv` | pattern, direction, account, tax_code, contact, review, note |
| `bank_lines.csv` | fingerprint, date, description, amount, direction, account, tax_code, contact, entry_id, imported_on |
| `finance_schedules.csv` | account, principal, annual_rate, months, balloon, payment, first_due, description |
| `company.json` | 회사 정보·세율·디렉터 (JSON) |

### 4.2 `source` 값 — BAS 계산이 여기 의존합니다

| 값 | 의미 |
|---|---|
| `INVOICE` / `RECEIPT` | 매출 인보이스 / 입금 |
| `BILL` / `BILL_PAYMENT` | 매입 청구서 / 지급 |
| `SPEND` / `RECEIVE` | 청구서 없는 즉시 지출·수입 |
| `BANK` | 은행 명세서 임포트 |
| `PAYROLL` / `SUPER` | 급여 / 연금 납부 |
| `BAS_PAYMENT` | **BAS 정산 — 캐시/발생 BAS 집계에서 반드시 제외됨** |
| `ASSET` / `FINANCE` | 자산 구입 / 할부 상환 |
| `DIVIDEND` / `DIRECTOR_LOAN` / `DEPRECIATION` / `JOURNAL` | 기타 |

⚠️ **`BAS_PAYMENT`을 제외하지 않으면 GST 계정 정리가 새 거래로 잡혀 다음 분기 BAS가 틀립니다.**

---

## 5. 인코딩된 호주 세무 규칙 — 함부로 바꾸지 마세요

| 규칙 | 값 | 위치 | 근거 |
|---|---|---|---|
| GST 신고기준 | **cash** | `config.gst_basis` | 이 회사 Activity Statement에 명시됨. 발생주의로 바꾸면 ATO 기대치와 안 맞음 |
| Super guarantee | 12% | `rates.super_rate` | 2025.7.1부터 |
| **Pay Day Super** | 2026.7.1~ | `periods.PAYDAY_SUPER_START` | 분기 납부 폐지, 급여일+7일 |
| 법인세 | 25% | `rates.company_tax_rate` | base rate entity |
| ABN 미제출 원천징수 | 47% | `rates.no_abn_withholding_rate` | |
| Car limit | $69,674 | `rates.car_limit` | 2025-26. **매년 물가연동됨 — 갱신 필요** |
| SGC 이자 | 10%/년 | `reports.SGC_INTEREST_RATE` | 분기 **시작일**부터 기산 |
| SGC 관리료 | $20/인/분기 | `reports.SGC_ADMIN_FEE_PER_EMPLOYEE` | |
| BAS 마감 (자체) | 10/28, 2/28, 4/28, 7/28 | `periods._QUARTERS` | |
| BAS 마감 (대리인) | 11/25, 2/28, 5/26, 8/25 | `periods._QUARTERS` | Q2는 연장 없음 |
| TPAR | 8/28 | `calendar_au` | 페인팅=건설업 |
| 법인세 신고 | 첫해 2/28, 이후 5/15 | `calendar_au` | |

### 특히 주의할 로직

**Cash 기준 BAS** (`reports._cash_events`)
인보이스 부분 입금 시 **원본 문서의 세금코드별로 안분**합니다.
과세+비과세 혼합 인보이스의 절반을 받으면 각각 절반씩 보고됩니다.

**Chattel mortgage GST** (`transactions.DIRECT_CASH_SOURCES`에 `ASSET` 포함)
할부금융은 금융사가 딜러에게 전액 대납하므로 **cash 기준이어도 GST 전액을 인도 분기에 공제**합니다.
이게 없으면 $4,725 공제를 60개월에 나눠 받게 됩니다.

**TPAR은 현금주의** (`reports.tpar`)
그 해에 실제 *지급한* 금액만. 부분 지급이면 원본 청구서 비율로 안분하고,
**원천징수액을 포함한 총액**을 보고합니다.

**Division 7A** (`reports.division_7a_warnings`)
현재 잔액뿐 아니라 **이미 마감된 회계연도 말 잔액**도 검사합니다. 후자가 진짜 위험입니다.

---

## 6. 통합 계획

### 6.0 동작하는 예제가 있습니다

`examples/` 에 실제로 돌아가는 구현이 들어있습니다. **먼저 돌려보세요.**

```bash
python3 examples/test_sql_backend.py   # 엔진 테스트 148개를 SQL 백엔드로 실행
python3 examples/test_api.py           # API 핸들러 테스트 16개
```

| 파일 | 내용 |
|---|---|
| `examples/schema.sql` | PostgreSQL 스키마 |
| `examples/sql_store.py` | `store.Table`의 SQL 구현 — `bind(conn, company_id)` 한 줄로 전환 |
| `examples/api.py` | 정산탭용 HTTP 핸들러 (프레임워크 무관) + FastAPI 배선 |

CSV를 SQL로 바꿔도 엔진이 그대로 돈다는 걸 실행으로 증명해뒀습니다.
자세한 건 [`examples/README.md`](../examples/README.md).

---

### 6.1 저장소를 DB로 — `store.py`만 교체

```python
class Table:
    def read(self) -> list[dict]
    def append(self, row: dict) -> None
    def append_many(self, rows: list[dict]) -> None
    def write_all(self, rows: list[dict]) -> None
    def find(self, **criteria) -> dict | None
    def next_sequence(self, field, prefix, width) -> str
```

이 6개 메서드만 같은 시그니처로 구현하면 나머지 코드는 그대로 돕니다.

**DB 전환 시 반드시 지킬 것:**
- 금액은 `DECIMAL(12,2)` — **`FLOAT`/`REAL` 절대 금지**
- `journal`은 **append-only**. UPDATE/DELETE 하지 마세요. 정정은 반대분개로.
- `(entry_id, line_no)` 유니크
- `next_sequence`는 동시성 문제가 있습니다. DB 시퀀스나 트랜잭션으로 감싸세요.
- `bank_lines.fingerprint` 유니크 인덱스 (중복 임포트 방지)

### 6.2 멀티테넌시

현재는 **단일 회사 전제**입니다. `store.data_dir()`이 환경변수 하나로 결정됩니다.

여러 회사를 지원하려면:
- `Table`에 `company_id` 컬럼 추가 + 모든 쿼리에 필터
- `config.load()`가 회사별 설정을 받도록 변경
- `accounts.CHART`는 전역 상수 — 회사별 커스텀 계정이 필요하면 DB로 옮겨야 합니다

### 6.3 성능

원장 전체를 매번 읽습니다. 현재 551줄이라 문제없지만:
- `ledger.all_lines()`에 캐시 추가 (append 시 무효화)
- 또는 `lines()`의 필터를 SQL WHERE로 내리기
- 리포트 결과는 **캐시하지 마세요** — 정합성이 이 시스템의 핵심 가치입니다

### 6.4 정산탭 화면 제안

```
┌─ 정산 ─────────────────────────────────────────┐
│ [현황]  통장 6,330 · 떼둘돈 4,377 · 쓸수있는돈 1,953  │  ← rp.cash_position
│                                                  │
│ ⚠ 확인 필요 (3)                                  │  ← cal.overdue + check 로직
│   • Q4 BAS 마감 지남 (2026-08-25)                │
│   • 연금 미납 $1,440 → SGC                       │
│   • TPAR: J Han 주소 없음                        │
│                                                  │
│ [명세서 올리기]  ← bankstatement + bankimport     │
│   → 자동분류 91건 / 확인필요 59건                  │
│      확인필요 큐에서 계정 선택 → post()            │
│                                                  │
│ [신고자료]  BAS · TPAR · SGC · STP               │  ← lodge.*_pack
│ [리포트]   손익 · 현금흐름 · 현장수익성            │  ← rp.*
└──────────────────────────────────────────────────┘
```

`cli.cmd_check()`의 로직을 그대로 옮기면 "확인 필요" 큐가 됩니다.

### 6.5 보안

- `web.py`는 **참조 구현**입니다. `ACCOUNTING_TOKEN` 방식은 앱 인증으로 교체하세요.
- 재무데이터입니다. 정산탭은 **디렉터 권한**으로 제한하세요.
- `data/`는 `.gitignore`에 있습니다. 실데이터를 커밋하지 마세요.
- 은행 명세서 PDF는 업로드 후 삭제하거나 암호화 저장하세요.

---

## 7. 하지 않는 것 / 알려진 한계

**의도적으로 안 하는 것 (사람이 판단해야 함)**
- 실제 ATO 제출 — 숫자만 산출합니다. 제출은 사람이 합니다
- STP 실시간 보고 — 급여 소프트웨어(Payroller/Xero) 영역
- 식사비의 접대비/현장간식 구분
- 디렉터 지급이 급여인지 대여금인지 판단
- 감가상각 유효수명·즉시상각 한도 판단

**미구현**
- 은행 자동연동 (수동 명세서 업로드만)
- CommBank 외 은행 파서 (`bankstatement.py`는 CommBank 포맷 전용)
- CSV 명세서 임포트 (PDF만)
- 외화
- 재고
- FBT 계산 (상용차 면제 전제)
- 이월결손금 · franking account 잔액 추적
- Payroll tax (NSW 임계치 $1.2m 미달)
- 다중 회사

**주의할 코너케이스**
- `bankstatement.py`는 잔액 증감으로 차변/대변을 판별합니다. 명세서 포맷이 바뀌면 파싱 실패합니다
  (조용히 틀리지 않고 `StatementError`를 던지도록 설계했습니다)
- `next_sequence`는 동시 접근에 안전하지 않습니다
- `accounts.CHART`는 코드 상수입니다. 계정 추가는 코드 수정이 필요합니다

---

## 8. 현재 장부 상태

```
회사        YOUR PAINTER SERVICE PTY LTD
ABN         74 694 601 413 / ACN 694 601 413
설립        2026-01-22
GST         등록, 분기 BAS, cash 기준
세무대리인   Woori Accounting Services (Kevin Park)
디렉터      Chungyeon Kim (Director+Secretary, 50%)
            Doyeob Kim (Director, 50%)

분개        207건 / 551줄
거래처      17
은행거래    202건 (2026-01-29 ~ 2026-07-30, CommBank 06 2194 10869266)
임포트규칙   11
```

**미결 사항 (인계 시점):**
1. Q4 FY2026 BAS 미신고 — $2,833.65, 마감 2026-08-25 경과
2. 연금 $1,440 미납 → SGC $1,543.12, 마감 2026-08-28 경과
3. TPAR FY2026 미신고 — J Han 주소 필요
4. STP finalisation FY2026 미완료
5. 차량 인도 대기 (2026-09-18 예정) — GST $4,725.09 공제 예정
6. 상호명이 개인(김청연) 명의 → 회사 이전 필요
7. 김청연 학생비자 근로시간 기록 부재

---

## 9. 실행·테스트

```bash
python3 -m unittest discover -s tests     # 148 tests
python3 -m accounting --help              # CLI 전체
```

의존성: **표준 라이브러리만.** 단
- PDF 명세서 파싱: `pypdf`
- 웹 대시보드: `flask`

테스트가 검증하는 것: GST 반올림, cash/accruals BAS 일치, BAS 라벨,
TPAR 현금주의 안분, 47% 원천징수, ABN 체크섬, Div 7A(현재+과거연도),
Pay Day Super, SGC, car limit, 할부 상환, 명세서 파싱·중복방지, 현금흐름 정합성.

**세율이나 규칙을 바꾸면 여기부터 돌리세요.**

---

## 10. 인계받은 뒤 첫 3가지

1. **`docs/accounting-au.md`를 읽으세요** — 사용자 관점의 전체 운영 가이드입니다
2. **`tests/test_accounting.py`를 읽으세요** — 세무 규칙이 실행 가능한 형태로 문서화돼 있습니다
3. **`store.py`를 DB로 교체**하고 테스트를 돌려보세요. 통과하면 나머지는 그대로 돕니다

질문이 생기면 각 모듈 상단 docstring에 "왜 이렇게 했는지"가 적혀 있습니다.
