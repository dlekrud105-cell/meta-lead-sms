# 회계 시스템 운영 가이드

호주(NSW) 페인팅 Pty Ltd — 디렉터 2명, GST 등록(분기 BAS), 하청업자(subcontractor) 사용 기준.

모든 명령은 레포 루트에서 실행합니다.

```bash
python3 -m accounting <명령>
```

---

## 1. 최초 셋업 (한 번만)

```bash
# 회사 정보 등록
python3 -m accounting setup \
  --name "Your Painting Pty Ltd" \
  --acn 123456789 \
  --abn 12345678901 \
  --registered 2026-01-15 \
  --director "디렉터1 이름" \
  --director "디렉터2 이름"

# 확인
python3 -m accounting company
python3 -m accounting accounts        # 계정과목 63개
python3 -m accounting accounts 서브    # 검색도 가능 (accounts subcontractor)
```

`--registered`는 **ASIC 등록일**입니다. 이 날짜에서 모든 신고 마감일이 계산되므로 정확히 넣어야 합니다.

장부는 `data/` 폴더에 CSV로 저장됩니다:

| 파일 | 내용 |
|---|---|
| `company.json` | 회사 정보, 세율, 디렉터 |
| `journal.csv` | **원장** — 모든 분개 (이게 원본 데이터) |
| `contacts.csv` | 고객·공급업체·하청업자 |
| `documents.csv` | 인보이스·청구서 대장 |
| `jobs.csv` | 현장/작업 |
| `accounts.csv` | 계정과목표 (참고용 출력) |

> `data/`는 `.gitignore`에 들어 있습니다. 실제 재무데이터를 git에 올리려면 그 줄을 지우세요.
> 다른 위치에 두려면 `export ACCOUNTING_DATA_DIR=/path/to/books`.

---

## 2. 거래처 · 현장 등록

```bash
# 고객
python3 -m accounting contact add "Jane Smith" customer --phone "0400 111 222"

# 하청업자 — ABN과 주소는 TPAR에 반드시 필요
python3 -m accounting contact add "Kim Painting" subcontractor \
  --abn 26008672179 --gst --address "5 Bay St, Rockdale NSW 2216"

# 공급업체
python3 -m accounting contact add "Bunnings" supplier --abn 26008672179 --gst

# 나중에 ABN 채워넣기
python3 -m accounting contact update C0003 --abn 12345678901 --address "..."

python3 -m accounting contact list
python3 -m accounting contact list --type subcontractor
```

**ABN 없는 하청업자에게 지급하면 47%를 원천징수해야 합니다.** 시스템이 자동으로 계산하고 경고합니다.

```bash
# 현장(job) — 현장별 수익성을 보려면 등록
python3 -m accounting job add "12 Smith St interior" --quoted 8800
python3 -m accounting job list
```

---

## 3. 일상 입력

### 매출 (인보이스)

금액은 **GST 제외(ex-GST)** 로 입력합니다. 시스템이 GST를 붙입니다.

```bash
python3 -m accounting invoice "Jane Smith" \
  4000:8000:"Interior repaint" \
  --date 2026-02-03 --job "12 Smith St interior"
# → INV0001  total 8,800.00 (GST 800.00)  due 2026-02-17
```

라인 형식: `계정코드:금액(ex-GST):설명:세금코드:현장`
여러 줄이면 그냥 나열: `4000:5000:"Walls" 4020:800:"Paint supplied"`

```bash
# 입금
python3 -m accounting receipt INV0001 --date 2026-02-20          # 전액
python3 -m accounting receipt INV0001 4400 --date 2026-02-20     # 일부
```

### 매입 (청구서)

```bash
# 하청업자 청구서
python3 -m accounting bill "Kim Painting" 5000:2000:"Labour 3 days" \
  --date 2026-02-05 --job "12 Smith St interior"

python3 -m accounting pay-bill BILL0001 --date 2026-02-12
```

### 바로 결제한 것 (청구서 없이)

여기는 **GST 포함 실제 결제금액**을 넣습니다. 영수증 금액 그대로.

```bash
python3 -m accounting spend 5100 550 --contact Bunnings --description "Dulux paint" --job "12 Smith St interior"
python3 -m accounting spend 6400 330 --description "Meta ads"
python3 -m accounting spend 6520 63 --tax-code FRE --description "ASIC fee"      # GST 없음
python3 -m accounting spend 6600 15 --tax-code INP --description "Bank fees"      # input taxed
python3 -m accounting spend 1400 2200 --tax-code CAP --description "Airless sprayer"  # 자산
python3 -m accounting spend 5100 88 --bank 2500 --description "Paint on card"     # 카드로 결제
```

### 현금 매출 (인보이스 없이)

```bash
python3 -m accounting receive 4030 440 --description "Callout - front door"
```

---

## 4. 디렉터 급여 · 배당 · 대여금

### 급여 (super 12% 자동 적립)

```bash
python3 -m accounting wages d1 2000 400 --date 2026-02-15
# gross 2,000.00  PAYG 400.00  net paid 1,600.00  super accrued 240.00
```

`d1`/`d2`는 디렉터 키입니다 (`company` 명령으로 확인). 이름으로도 됩니다.
급여를 주면 **그 날짜 이전에 STP로 ATO에 보고**해야 합니다.

### Super 실제 납부

```bash
python3 -m accounting super 960 --date 2026-04-20
```

Super는 분기 마감일에 **펀드에 도착**해 있어야 합니다. 늦으면 SGC로 바뀌고 손금불산입됩니다. 마감일 최소 일주일 전에 보내세요.

### 배당

```bash
python3 -m accounting dividend d1 5000 --date 2026-06-30            # franked
python3 -m accounting dividend d2 5000 --unfranked
```

### 대여금 (⚠️ Division 7A)

```bash
python3 -m accounting loan d1 3000              # 회사 → 디렉터 (위험)
python3 -m accounting loan d1 3000 --repay      # 디렉터 → 회사 (상환)

python3 -m accounting report loans
```

회사 돈을 급여도 배당도 아닌 형태로 가져가면 전부 여기로 쌓입니다.
**6월 30일 시점 잔액이 남아 있고 법인세 신고기한까지 상환하지 않으면, 적격 대여계약(7년·벤치마크 이자)이 없는 한 ATO가 비적격 배당으로 간주과세합니다.**
`check` 명령이 지난 회계연도 마감 시점 잔액까지 잡아냅니다.

---

## 5. 분기 업무 (BAS)

```bash
# 1) 숫자 확인
python3 -m accounting report bas --period 2026Q3
```

```
  Label  Description                               Amount
  G1     Total sales (including GST)             30,800.00
  G10    Capital purchases (including GST)        2,200.00
  G11    Non-capital purchases (including GST)   11,078.00
  1A     GST on sales                             2,800.00
  1B     GST on purchases                         1,200.00
  W1     Total salary, wages and other payments   8,000.00
  W2     Amounts withheld from W1                 1,600.00
  W4     Amounts withheld where no ABN quoted         0.00
  7      NET AMOUNT PAYABLE                       3,200.00
```

2) 이 숫자를 **ATO 온라인(myGovID/RAM) 또는 세무대리인**을 통해 그대로 입력합니다.
3) ATO가 PAYG 분납(5A)을 통지했다면 `--instalment 1200`으로 넣으세요.
4) 납부 후 장부 반영:

```bash
python3 -m accounting report bas --period 2026Q3 --pay --pay-date 2026-04-28
```

`--pay`는 GST 계정을 정리하고 은행에서 순액을 빼는 분개를 자동으로 만듭니다. 이 분개는 다음 분기 BAS 집계에서 자동 제외됩니다.

**분기 마감일** (자체신고 기준, 세무대리인 쓰면 대체로 4주 연장):

| 분기 | 기간 | BAS·Super 마감 |
|---|---|---|
| Q1 | 7–9월 | 10/28 |
| Q2 | 10–12월 | 2/28 |
| Q3 | 1–3월 | 4/28 |
| Q4 | 4–6월 | 7/28 |

---

## 6. 연간 업무

```bash
# TPAR — 하청업자 지급 신고 (페인팅 = 건설업, 매년 8/28 마감)
python3 -m accounting report tpar --fy 2026
```

TPAR은 **현금주의**입니다: 그 해에 실제로 *지급한* 금액만 들어갑니다(청구서만 받고 안 준 건 제외). 총액은 GST와 원천징수액을 포함합니다. ABN·주소가 빠진 payee는 `Issues`에 표시됩니다.

```bash
# 결산용
python3 -m accounting report pl --period FY2026
python3 -m accounting report bs --to 2026-06-30
python3 -m accounting depreciate 1400 400 --date 2026-06-30   # 감가상각
python3 -m accounting report tax --fy 2026                     # 법인세 추정
```

**연간 마감일**

| 신고 | 마감 |
|---|---|
| STP finalisation | 7/14 |
| TPAR | 8/28 |
| 법인세 신고 (신규 등록 법인 첫 해) | 다음해 2/28 |
| 법인세 신고 (그 이후, 세무대리인) | 다음해 5/15 |
| ASIC annual review | 설립 기념일 |

---

## 7. 매주 한 번: `check`

이 한 줄이 "지금 문제가 뭔가"를 다 알려줍니다.

```bash
python3 -m accounting check
```

잡아내는 것:
- 대차 불일치
- 지난 신고 마감 (BAS / super / TPAR / STP / 법인세 / ASIC)
- Division 7A 노출 — 마감된 회계연도 잔액까지
- ABN 없는 하청업자
- TPAR에 필요한 정보가 빠진 payee
- 60일 넘은 미수금
- **ATO·super에 줄 돈보다 통장 잔고가 적은 상황**
- GST 등록 임계치($75,000) 도달 (미등록인 경우)

문제가 있으면 exit code 1로 끝나므로 cron에 걸어도 됩니다.

```bash
python3 -m accounting calendar --days 120 --detail   # 마감일 전체
python3 -m accounting report cash                     # 지금 쓸 수 있는 돈
```

---

## 8. 리포트 목록

```bash
python3 -m accounting report <이름> [--period FY2026 | --from ... --to ...]
```

| 이름 | 내용 |
|---|---|
| `tb` | 시산표 |
| `pl` | 손익계산서 (손금불산입 add-back 포함) |
| `bs` | 재무상태표 |
| `bas` | BAS 라벨 |
| `tpar` | 하청업자 지급 신고 |
| `ar` / `ap` | 미수금 / 미지급금 연령분석 |
| `jobs` | 현장별 수익성 |
| `cash` | 현금 포지션 + 떼둬야 할 금액 |
| `tax` | 법인세 추정 |
| `loans` | 디렉터 대여금 + Div 7A |

기간 지정: `--period FY2026`, `--period 2026Q3`, `--from 2026-01-01 --to 2026-03-31`

---

## 9. GST 세금코드

| 코드 | 의미 | 쓰는 곳 |
|---|---|---|
| `GST` | 10% 과세 | 대부분의 매출·매입 |
| `CAP` | 10% 과세, 자본적 지출 | 공구·차량 등 자산 (G10) |
| `FRE` | GST-free | ASIC 수수료, 정부 부과금, 면허 |
| `INP` | Input taxed | 은행 수수료·이자 (매입세액 공제 불가) |
| `NT` | BAS 제외 | 급여, super, PAYG, 배당, 대여금, 감가상각, ATO 납부 |

계정마다 기본 코드가 있어서 보통은 생략해도 됩니다. 다를 때만 `--tax-code`로 지정하세요.

**차량 등록증(rego)처럼 섞인 청구서**는 나눠서 입력하세요 — CTP·보험은 GST 있고, 등록비·검사비는 GST-free입니다.

---

## 10. 통장 관리 규칙

입금 받을 때마다 별도 저축계좌(`1010`)로 옮기세요:

- **1/11** → GST
- **이익의 25%** → 법인세
- **급여의 12%** → super

`report cash`가 지금 얼마를 떼둬야 하고 얼마를 써도 되는지 계산해줍니다.

---

## 11. 웹 대시보드 (선택)

```bash
export ACCOUNTING_TOKEN="긴-랜덤-문자열"
export ACCOUNTING_DATA_DIR=/path/to/books
```

Flask 앱(`app.py`)에 자동으로 `/accounting`이 붙습니다. 접속: `https<앱주소>/accounting/?token=...`

- **읽기 전용**입니다. 입력은 CLI에서만 합니다.
- `ACCOUNTING_TOKEN`이 없으면 아예 마운트되지 않습니다.
- Heroku/Railway 같은 곳은 파일시스템이 휘발성이라 `ACCOUNTING_DATA_DIR`을 볼륨으로 지정하거나, 대시보드는 읽기 전용 미러로만 쓰세요.

---

## 12. 테스트

```bash
python3 -m unittest discover -s tests
```

GST·BAS·TPAR·원천징수·Div 7A·마감일 계산을 52개 테스트로 검증합니다. 세율이나 규칙을 바꿨다면 여기부터 돌려보세요.

세율은 `data/company.json`의 `rates`에 있습니다:

```json
"rates": {
  "super_rate": "0.12",
  "company_tax_rate": "0.25",
  "no_abn_withholding_rate": "0.47",
  "gst_rate": "0.10"
}
```

---

## 13. 이 시스템이 하지 않는 것

직접 처리해야 하거나 확인이 필요한 부분입니다.

- **실제 신고 제출** — BAS·TPAR·법인세는 ATO 온라인 서비스나 세무대리인을 통해 사람이 제출합니다. 이 시스템은 넣을 숫자를 만들어줍니다.
- **STP 보고** — 급여 지급 시마다 ATO에 실시간 보고가 필요합니다. STP 지원 급여 소프트웨어나 세무대리인이 필요합니다.
- **은행 자동 연동** — 거래는 수동 입력입니다. 은행 명세서와 대조(reconcile)는 직접 하세요.
- **FBT** — 회사 차를 사적으로 쓰면 FBT 대상일 수 있습니다 (FBT 연도 4/1–3/31, 신고 5/21).
- **Payroll tax** — NSW 임계치 $1.2m. 넘을 일이 생기면 별도 등록 필요.
- **Workers compensation (icare NSW)** — 연간 급여 $7,500 초과 시 필수. 보험사에 연간 급여 신고 별도.
- **면허 관련** — NSW에서 $5,000 초과 페인팅 작업은 NSW Fair Trading 면허, 주거용 $20,000 초과는 HBCF 보험이 필요합니다.
- **감가상각 스케줄** — `depreciate` 명령은 금액을 넣으면 기록만 합니다. 유효수명·즉시상각 한도 판단은 직접 하세요.
- **이월결손금 · franking account 잔액** — 법인세 신고 시 별도 관리가 필요합니다.

세율과 마감일은 FY2025-26 기준입니다. 실제 신고 전 등록 세무대리인(registered tax agent) 확인을 권합니다.
