# 회계 시스템 운영 가이드

호주(NSW) 페인팅 Pty Ltd — 디렉터 2명, GST 등록(분기 BAS), 하청업자(subcontractor) 사용 기준.

> **GST 신고 기준(cash/accruals)을 반드시 확인하세요.** Activity Statement의
> `GST accounting method` 항목에 나옵니다. YOUR PAINTER SERVICE PTY LTD는 **Cash**입니다.
> 기준이 다르면 BAS 숫자가 ATO 기대치와 안 맞습니다.

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
  --director "디렉터2 이름" \
  --gst-basis cash \
  --tax-agent "Woori Accounting Services"

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
| `lodgements.csv` | 신고 완료 기록 (회계사가 이미 낸 것) |
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

## 2-1. 은행 명세서 자동 입력 (제일 빠른 방법)

CommBank PDF 명세서를 그대로 읽어서 분류합니다. 하나하나 입력할 필요가 없습니다.

```bash
# 1) 미리보기 — 아무것도 기록되지 않습니다
python3 -m accounting import-bank statement.pdf

# 2) 판단이 필요한 것만 보기
python3 -m accounting import-bank statement.pdf --review

# 3) 확실한 것만 기록
python3 -m accounting import-bank statement.pdf --post

# 4) 검토 항목까지 전부 기록
python3 -m accounting import-bank statement.pdf --post --include-review
```

**안전장치:**
- 명세서를 읽으면 **은행이 인쇄한 차변·대변 합계와 대조**합니다. 안 맞으면 아예 거부하고 중단합니다.
- 같은 거래는 **두 번 들어가지 않습니다** (날짜+금액+잔액+내용 지문으로 판별)
- 애매한 건 `REVIEW`로 표시하고 **기록하지 않습니다**. 식사비가 접대비인지 현장 간식인지 같은 판단은 사람이 해야 합니다.

**규칙 가르치기:**

```bash
python3 -m accounting rule add "DULUX" 5100
python3 -m accounting rule add "ACTIVE BUILDING GROUP" 4010 --direction credit
python3 -m accounting rule add "re:Transfer To J HAN" 5000 --contact "J Han"
python3 -m accounting rule list
```

`re:`로 시작하면 정규식입니다. 내가 추가한 규칙이 기본 규칙보다 먼저 적용됩니다.
`--direction credit`은 입금에만, `debit`은 출금에만 적용합니다.

> 하청업자 계정(5000)으로 분류되면 **연락처가 자동 생성**됩니다. TPAR 신고에 필요하기 때문입니다.
> 나중에 `contact update`로 ABN과 주소를 채워넣으세요.

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

## 3-1. 차량·장비 구입 (할부금융 포함)

차량 계약서는 **하나의 과세금액이 아닙니다.** 인지세(stamp duty)와 등록비는 GST가 없지만
자산 원가에는 포함됩니다. 그래서 나눠서 입력합니다.

```bash
python3 -m accounting buy-asset 1420 47250.91 \
  --gst 4725.09 \
  --gst-free 1769.00 \
  --deposit 2000.00 \
  --financed 51745.00 \
  --contact "AAG Ryde Hyundai" \
  --description "Hyundai Staria Load HEV - deal WHR2732" \
  --date 2026-09-18
```

| 인자 | 뜻 |
|---|---|
| `taxable` | GST 붙는 항목의 **GST 제외** 금액 |
| `--gst-free` | 인지세·등록비 등 GST 없는 항목 (자산 원가에 포함됨) |
| `--deposit` | 지금 통장에서 나간 돈 |
| `--financed` | 할부금융으로 넘어간 잔액 |

**Chattel mortgage(할부금융)에서 중요한 점:**
- 차량 **소유권은 처음부터 회사**입니다. 금융사는 돈만 빌려준 겁니다.
- 그래서 **GST 전액을 인도받은 분기에 한 번에 공제**합니다. Cash 기준이어도 그렇습니다
  (금융사가 딜러에게 대신 전액 지급했으므로 지급한 것으로 봄).
- 매달 상환금은 **원금과 이자를 나눠야** 합니다. 전액을 비용 처리하면 차를 두 번 공제하는 셈입니다.

**상환 스케줄을 한 번 만들어두면 매달 자동으로 갈립니다:**

```bash
python3 -m accounting finance-schedule --account 2800 \
  --principal 51745.00 --rate 9.3 --months 60 --start 2026-10-18

python3 -m accounting finance-payment --auto --account 2800 --date 2026-10-18
#   Instalment #1 due 2026-10-18
#   principal 680.67  interest 401.02  loan balance 51,064.33
```

잔가(balloon)가 있으면 `--balloon 10000`을 붙이세요. 이자만 비용(6900)이고,
원금은 부채(2800)를 줄입니다.

**인도 전 계약금은 따로 잡습니다:**

```bash
python3 -m accounting asset-deposit 2000.00 --date 2026-09-05 \
  --description "Van deposit"
# -> 1210 Deposits Paid on Assets 에 보관
```

인도받기 전에는 **GST를 공제하지 않고 감가상각도 시작하지 않습니다.**
인도일에 `buy-asset --deposit-account 1210`으로 자산에 편입시킵니다.

**Car limit(차량 감가상각 한도)** — 2025-26년 $69,674. 이걸 넘으면 감가상각과 GST 공제가
상한에 걸립니다. 단, **적재중량 1톤 이상 또는 주로 승객용으로 설계되지 않은 차량은
"car"가 아니라서 한도가 적용되지 않습니다.** 시스템이 초과 시 경고합니다.

**FBT** — 유트·밴 같은 상용차는 사적 사용이 제한적이면 FBT 면제입니다
(출퇴근 + "minor, infrequent and irregular" 수준). ATO PCG 2018/3 기준으로
연간 사적 주행 1,000km 이하, 1회 왕복 200km 이하가 안전선입니다.
**주행기록(logbook)을 남기세요.** 면제를 주장하려면 근거가 필요합니다.

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

## 4-1. Cash vs Accruals — 이게 왜 중요한가

| | Cash 기준 | Accruals 기준 |
|---|---|---|
| GST 신고 시점 | **돈이 실제로 오갈 때** | 인보이스 발행/수취 시점 |
| 미수금에 붙은 GST | 아직 안 냄 | 이미 냄 |
| 현금흐름 | 유리 | 불리 |

우리 회사는 **Cash**입니다. 그래서:

- 인보이스를 끊어도 **입금 전에는 BAS에 안 올라갑니다**
- 부분 입금이면 그 비율만큼만 올라갑니다 (세금코드별로 안분됨)
- 청구서를 받아도 **지급 전에는 매입세액 공제를 못 받습니다**

```bash
python3 -m accounting report bas --period 2026Q3
```

출력 하단에 아직 신고 대상이 아닌 금액이 따로 나옵니다:

```
  Not on this BAS because the money has not moved yet:
    GST on unpaid invoices you issued      800.00  (payable when they pay you)
    GST credits on bills you owe           200.00  (claimable when you pay)
```

기준을 바꿔서 비교해보려면 `--basis accruals`를 붙이세요.

---

## 4-1-1. ABN 검증

ABN에는 체크섬이 있습니다. 시스템이 자동으로 검사해서 오타나 허위 ABN을 걸러냅니다.

```bash
python3 -m accounting contact update "J Han" --abn 60280356376 --gst
# 오타면 거부됩니다:
#   error: 60 280 356 377 fails the ABN checksum
```

**체크섬 통과 = 형식이 맞다는 뜻일 뿐입니다.** 그 ABN이 실제로 활성 상태인지, 그 사람 것이 맞는지,
GST 등록이 되어 있는지는 [ABN Lookup](https://abr.business.gov.au)에서 따로 확인하세요.
GST 미등록 업체가 GST를 청구하면 그 매입세액은 공제받을 수 없습니다.

유효한 ABN을 제시하지 않은 하청업자에게는 **47% 원천징수**가 적용되며, `check`가 잡아냅니다.

---

## 4-2. Pay Day Super (2026년 7월 1일 시행)

**분기별 연금 납부는 끝났습니다.** 이제 급여를 줄 때마다 7일 이내에 펀드에 도착해야 합니다.

```bash
python3 -m accounting report super
```

```
  Pay date    Super  Due         Paid  Outstanding  Status
  2026-07-20  240.00 2026-07-27  0.00       240.00  LATE
```

시스템이 급여일 기준으로 마감일을 계산하고, `check`에서 늦은 건을 잡아냅니다.
2026년 6월 30일 이전 급여는 기존 분기 규칙(28일)으로 계산됩니다.

**연금을 기한 내에 못 냈다면 — SGC**

기한이 지나면 그냥 늦게 내는 게 아니라 **SGC(Superannuation Guarantee Charge)** 로 성격이 바뀝니다.

```
SGC = 미납 연금 + 명목이자(연 10%, 분기 시작일부터) + 관리수수료($20/인/분기)
```

- **SGC는 전액 손금불산입입니다.** 제때 냈으면 공제받았을 금액까지 잃습니다.
- 펀드에 지금 입금해도 해결되지 않습니다. **SGC statement를 ATO에 별도로 제출**해야 합니다.
- SGC는 **디렉터 개인책임(DPN)** 대상입니다.

`report super`가 분기별로 자동 계산해서 보여줍니다.

> 급여를 아예 안 주는 회사면 super·STP 마감일이 표시되지 않습니다. 급여를 처음 지급하는
> 순간부터 자동으로 나타납니다.

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

## 6-0. 직접 신고하기 — `lodge`

ATO 화면에 그대로 옮겨 적을 수 있는 형태로 뽑아줍니다.

```bash
python3 -m accounting lodge bas  --period 2026Q4
python3 -m accounting lodge tpar --fy 2026
python3 -m accounting lodge sgc                 # 연금 미납분
python3 -m accounting lodge stp  --fy 2026      # 연말정산 확정
```

각 항목마다 **ATO 폼 라벨 순서대로**, 넣을 숫자와 그 숫자의 출처를 같이 보여줍니다.
BAS는 센트를 버리고 정수로 나옵니다 (ATO 양식이 정수만 받습니다).

**어디서 하나:**

| 신고 | 위치 |
|---|---|
| BAS | ATO Online services for business → Lodgments → Activity statements |
| TPAR | 같은 곳 → Taxable payments annual report |
| SGC | 같은 곳 → Super guarantee charge statement |
| STP | 급여 소프트웨어(Payroller/Xero)에서. ATO 사이트 아님 |

> **이건 옮겨적기 도우미입니다. ATO에 아무것도 전송하지 않습니다.**
> 신고서의 선언(declaration)에 서명하는 건 권한 있는 사람이고, 책임도 그 사람에게 있습니다.

신고를 마치면 기록하세요:

```bash
python3 -m accounting lodged BAS "Q4 FY2026" --date 2026-09-10 --ref <접수번호>
```

---

## 6-1. 회계사가 이미 신고한 것 기록하기

세무대리인이 대신 신고했으면 시스템은 그걸 모릅니다. 기록해두면 마감 알림에서 사라집니다.

```bash
python3 -m accounting lodged BAS "Q3 FY2026" \
  --date 2026-05-30 --ref 59741490849 --amount 68 \
  --by "Woori Accounting Services"

python3 -m accounting lodgements          # 지금까지 신고한 것 전체
python3 -m accounting lodged BAS "Q3 FY2026" --undo   # 잘못 넣었으면
```

`kind`는 `BAS` / `TPAR` / `STP` / `TAX_RETURN` / `ASIC`,
`period`는 `"Q3 FY2026"` / `"FY2026"` / `"2027"` 형식입니다.

**세무대리인 마감일 연장**은 `--tax-agent`를 설정하면 자동 적용됩니다:

| 분기 | 자체신고 | 세무대리인 |
|---|---|---|
| Q1 (7–9월) | 10/28 | **11/25** |
| Q2 (10–12월) | 2/28 | 2/28 (연장 없음) |
| Q3 (1–3월) | 4/28 | **5/26** |
| Q4 (4–6월) | 7/28 | **8/25** |

---

## 6-2. 회계사 양식으로 내보내기

세무대리인이 준 엑셀 양식을 그대로 채워줍니다. 양식의 헤더·거래유형·산식은 건드리지 않습니다.

```bash
python3 -m accounting export-xlsx Bookkeeping_Template.xlsx 채워진파일.xlsx
python3 -m accounting export-xlsx 양식.xlsx 출력.xlsx --period 2026Q4
```

**채우는 방식:**

| 열 | 내용 |
|---|---|
| A 날짜 | 은행 거래일 |
| B 거래 유형 | `수입` / `비용` / **`기타`** |
| C 설명 | 은행 명세서 원문 (+ 거래처명) |
| D 금액 | 실제 통장에서 오간 금액 (GST 포함) |
| E GST 포함 여부 | `Y` / `N` |
| F GST 금액 | **산식** `=IF(E2="Y",ROUND(D2/11,2),0)` |
| G 순금액 | **산식** `=D2-F2` |
| H 지불 방법 | 카드 / 계좌이체 / 자동이체 (명세서 문구에서 판별) |
| I 카테고리 | 계정과목 (손금불산입 항목은 표시됨) |

**`기타`가 왜 필요한가** — 디렉터 대여금 입출금이나 ATO BAS 납부는 손익이 아닙니다.
이걸 수입이나 비용에 넣으면 **순이익이 왜곡됩니다.** 그래서 별도로 분리하고,
Summary 시트 아래쪽에 대조용 항목을 추가합니다 (원본 산식 4개는 그대로 둡니다).

F·G열은 하드코딩이 아니라 **산식**이라, 금액을 고치면 GST와 순금액이 따라 바뀝니다.

---

## 7. 매주 한 번: `check`

이 한 줄이 "지금 문제가 뭔가"를 다 알려줍니다.

```bash
python3 -m accounting check
```

잡아내는 것:
- 대차 불일치
- 지난 신고 마감 (BAS / super / TPAR / STP / 법인세 / ASIC) — 이미 신고 기록한 건 제외
- **Pay Day Super 지연** (급여일 + 7일 초과)
- **연금 미적립** — 급여는 나갔는데 super 12%가 안 잡힌 경우 (연도별로)
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
| `cashflow` | **월별 현금흐름** — 얼마 들어오고 나갔는지 |
| `super` | 급여별 연금 납부 현황 + 지연 |
| `loans` | 디렉터 대여금 + Div 7A |

기간 지정: `--period FY2026`, `--period 2026Q3`, `--from 2026-01-01 --to 2026-03-31`

**월별 현금흐름 보기:**

```bash
python3 -m accounting report cashflow --from 2026-01-01 --to 2026-07-31
```

이익이 아니라 **통장 기준**입니다. 각 입출금을 반대편 계정으로 분류해서,
고객 입금은 매출로, 디렉터가 넣은 돈은 대여금으로 따로 보여줍니다 —
"전부 이체"로 뭉뚱그리지 않습니다. 인보이스 입금은 원본 인보이스까지 추적해서
어떤 매출인지 표시합니다.

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
