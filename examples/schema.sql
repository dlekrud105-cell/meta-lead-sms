-- Accounting engine schema (PostgreSQL 14+)
--
-- Mirrors the CSV tables in accounting/store.py. Read that module and
-- docs/HANDOVER.md before changing anything here.
--
-- Three rules this schema exists to enforce:
--   1. Money is DECIMAL. Never FLOAT - a cent lost to binary rounding is a
--      trial balance that does not balance.
--   2. The journal is append-only. Corrections are reversing entries, never
--      an UPDATE, because the audit trail is the point.
--   3. An imported bank line can only land once, whatever the user uploads.

BEGIN;

CREATE SCHEMA IF NOT EXISTS accounting;
SET search_path TO accounting, public;

-- ---------------------------------------------------------------- tenancy
-- The engine currently assumes one company. company_id is here so the app
-- can host several without a migration later; every query must filter on it.

CREATE TABLE company (
    company_id          BIGSERIAL PRIMARY KEY,
    name                TEXT        NOT NULL,
    trading_name        TEXT        NOT NULL DEFAULT '',
    abn                 VARCHAR(11) NOT NULL DEFAULT '',
    acn                 VARCHAR(9)  NOT NULL DEFAULT '',
    state               TEXT        NOT NULL DEFAULT 'NSW',
    address             TEXT        NOT NULL DEFAULT '',
    registered_date     DATE,                       -- ASIC registration
    gst_registered      BOOLEAN     NOT NULL DEFAULT TRUE,
    gst_cycle           TEXT        NOT NULL DEFAULT 'quarterly',
    -- Must match the GST accounting method printed on the activity statement.
    gst_basis           TEXT        NOT NULL DEFAULT 'cash'
                        CHECK (gst_basis IN ('cash', 'accruals')),
    uses_tax_agent      BOOLEAN     NOT NULL DEFAULT FALSE,
    tax_agent           TEXT        NOT NULL DEFAULT '',
    base_rate_entity    BOOLEAN     NOT NULL DEFAULT TRUE,
    reports_tpar        BOOLEAN     NOT NULL DEFAULT TRUE,
    default_bank        VARCHAR(8)  NOT NULL DEFAULT '1000',
    savings_bank        VARCHAR(8)  NOT NULL DEFAULT '1010',
    invoice_terms_days  INTEGER     NOT NULL DEFAULT 14,
    -- Rates that change with the law: super_rate, company_tax_rate,
    -- no_abn_withholding_rate, gst_rate, car_limit. Stored as text so they
    -- round-trip into Decimal without passing through a float.
    rates               JSONB       NOT NULL DEFAULT '{}'::jsonb,
    directors           JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --------------------------------------------------------------- contacts

CREATE TABLE contact (
    company_id      BIGINT      NOT NULL REFERENCES company(company_id),
    contact_id      VARCHAR(16) NOT NULL,
    name            TEXT        NOT NULL,
    type            TEXT        NOT NULL DEFAULT 'other'
                    CHECK (type IN ('customer','supplier','subcontractor',
                                    'director','ato','other')),
    -- Digits only. The checksum is validated in accounting/abn.py; an ABN
    -- that fails it must not be stored as quoted.
    abn             VARCHAR(11) NOT NULL DEFAULT '',
    abn_quoted      BOOLEAN     NOT NULL DEFAULT FALSE,
    gst_registered  BOOLEAN     NOT NULL DEFAULT FALSE,
    email           TEXT        NOT NULL DEFAULT '',
    phone           TEXT        NOT NULL DEFAULT '',
    address         TEXT        NOT NULL DEFAULT '',
    notes           TEXT        NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, contact_id),
    CONSTRAINT contact_abn_digits CHECK (abn = '' OR abn ~ '^[0-9]{11}$')
);

CREATE UNIQUE INDEX contact_name_unique
    ON contact (company_id, lower(name));
-- The TPAR needs every subcontractor findable by type.
CREATE INDEX contact_by_type ON contact (company_id, type);

-- ------------------------------------------------------------------- jobs

CREATE TABLE job (
    company_id   BIGINT      NOT NULL REFERENCES company(company_id),
    job_id       VARCHAR(16) NOT NULL,
    name         TEXT        NOT NULL,
    contact_id   VARCHAR(16) NOT NULL DEFAULT '',
    address      TEXT        NOT NULL DEFAULT '',
    status       TEXT        NOT NULL DEFAULT 'active'
                 CHECK (status IN ('quoted','active','complete','lost')),
    quoted_incl  DECIMAL(12,2),
    started      DATE,
    completed    DATE,
    notes        TEXT        NOT NULL DEFAULT '',
    PRIMARY KEY (company_id, job_id)
);

CREATE UNIQUE INDEX job_name_unique ON job (company_id, lower(name));

-- -------------------------------------------------------------- documents
-- Invoices and bills. The amounts here are the document's face value; what
-- is still outstanding is derived from the journal, never stored.

CREATE TABLE document (
    company_id   BIGINT      NOT NULL REFERENCES company(company_id),
    doc_id       VARCHAR(24) NOT NULL,
    type         TEXT        NOT NULL CHECK (type IN ('INVOICE','BILL')),
    date         DATE        NOT NULL,
    due_date     DATE,
    contact_id   VARCHAR(16) NOT NULL DEFAULT '',
    job_id       VARCHAR(16) NOT NULL DEFAULT '',
    description  TEXT        NOT NULL DEFAULT '',
    total_incl   DECIMAL(12,2) NOT NULL,
    gst          DECIMAL(12,2) NOT NULL DEFAULT 0,
    -- 47% withheld when a supplier has not quoted a valid ABN.
    withheld     DECIMAL(12,2) NOT NULL DEFAULT 0,
    entry_id     VARCHAR(16) NOT NULL,
    PRIMARY KEY (company_id, doc_id),
    CONSTRAINT document_amounts_sane
        CHECK (total_incl >= 0 AND gst >= 0 AND withheld >= 0
               AND withheld <= total_incl)
);

CREATE INDEX document_open ON document (company_id, type, date);
CREATE INDEX document_by_contact ON document (company_id, contact_id);

-- ---------------------------------------------------------------- journal
-- The only place data actually lives. Everything else in this schema is a
-- register that points back at it.

CREATE TABLE journal_line (
    company_id   BIGINT      NOT NULL REFERENCES company(company_id),
    entry_id     VARCHAR(16) NOT NULL,
    line_no      SMALLINT    NOT NULL,
    date         DATE        NOT NULL,
    memo         TEXT        NOT NULL DEFAULT '',
    -- Drives report behaviour. BAS_PAYMENT in particular MUST be excluded
    -- when measuring GST movement, or clearing the GST accounts reads as new
    -- activity and the next quarter's BAS is wrong.
    source       TEXT        NOT NULL DEFAULT 'JOURNAL'
                 CHECK (source IN ('INVOICE','RECEIPT','BILL','BILL_PAYMENT',
                                   'SPEND','RECEIVE','BANK','PAYROLL','SUPER',
                                   'BAS_PAYMENT','DIVIDEND','DIRECTOR_LOAN',
                                   'DEPRECIATION','ASSET','FINANCE','JOURNAL')),
    doc_ref      VARCHAR(24) NOT NULL DEFAULT '',
    account      VARCHAR(8)  NOT NULL,
    description  TEXT        NOT NULL DEFAULT '',
    debit        DECIMAL(12,2) NOT NULL DEFAULT 0,
    credit       DECIMAL(12,2) NOT NULL DEFAULT 0,
    tax_code     VARCHAR(8)  NOT NULL DEFAULT 'NT'
                 CHECK (tax_code IN ('GST','CAP','FRE','INP','NT')),
    contact      VARCHAR(16) NOT NULL DEFAULT '',
    job          VARCHAR(16) NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, entry_id, line_no),
    -- One side or the other, never both, never zero, never negative. The
    -- same rule ledger.Entry.validate() applies before posting.
    CONSTRAINT journal_one_sided CHECK (
        debit >= 0 AND credit >= 0
        AND (debit = 0) <> (credit = 0)
    )
);

-- The query patterns in accounting/ledger.py, in order of how hot they are.
CREATE INDEX journal_by_date     ON journal_line (company_id, date);
CREATE INDEX journal_by_account  ON journal_line (company_id, account, date);
CREATE INDEX journal_by_doc      ON journal_line (company_id, doc_ref)
                                  WHERE doc_ref <> '';
CREATE INDEX journal_by_source   ON journal_line (company_id, source, date);
CREATE INDEX journal_by_contact  ON journal_line (company_id, contact)
                                  WHERE contact <> '';
CREATE INDEX journal_by_job      ON journal_line (company_id, job)
                                  WHERE job <> '';

-- Append-only. Post a reversing entry instead of editing history.
CREATE OR REPLACE FUNCTION journal_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'journal_line is append-only: post a reversing entry instead of %',
        TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER journal_no_update BEFORE UPDATE OR DELETE ON journal_line
    FOR EACH ROW EXECUTE FUNCTION journal_is_append_only();

-- Debits must equal credits per entry. Deferred so a multi-line entry can be
-- inserted row by row inside one transaction and checked at COMMIT.
CREATE TABLE journal_entry_balance (
    company_id     BIGINT      NOT NULL,
    entry_id       VARCHAR(16) NOT NULL,
    total_debit    DECIMAL(12,2) NOT NULL DEFAULT 0,
    total_credit   DECIMAL(12,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (company_id, entry_id),
    CONSTRAINT entry_balances CHECK (total_debit = total_credit)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE OR REPLACE FUNCTION journal_track_balance() RETURNS trigger AS $$
BEGIN
    INSERT INTO journal_entry_balance AS b
        (company_id, entry_id, total_debit, total_credit)
    VALUES (NEW.company_id, NEW.entry_id, NEW.debit, NEW.credit)
    ON CONFLICT (company_id, entry_id) DO UPDATE
        SET total_debit  = b.total_debit  + EXCLUDED.total_debit,
            total_credit = b.total_credit + EXCLUDED.total_credit;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER journal_balance_check AFTER INSERT ON journal_line
    FOR EACH ROW EXECUTE FUNCTION journal_track_balance();

-- ------------------------------------------------------------- bank lines
-- What has been imported from a statement, so it cannot be imported twice.

CREATE TABLE bank_line (
    company_id   BIGINT      NOT NULL REFERENCES company(company_id),
    -- sha256 of date|amount|direction|balance|description, first 16 chars.
    -- The balance is in there so two identical purchases on one day still
    -- get different fingerprints.
    fingerprint  VARCHAR(32) NOT NULL,
    date         DATE        NOT NULL,
    description  TEXT        NOT NULL,
    amount       DECIMAL(12,2) NOT NULL CHECK (amount > 0),
    direction    TEXT        NOT NULL CHECK (direction IN ('debit','credit')),
    account      VARCHAR(8)  NOT NULL,
    tax_code     VARCHAR(8)  NOT NULL DEFAULT 'NT',
    contact      VARCHAR(16) NOT NULL DEFAULT '',
    entry_id     VARCHAR(16) NOT NULL,
    imported_on  DATE        NOT NULL DEFAULT CURRENT_DATE,
    PRIMARY KEY (company_id, fingerprint)
);

CREATE INDEX bank_line_by_date ON bank_line (company_id, date);

-- ----------------------------------------------------------- import rules
-- Merchant text to account. Checked before the built-in rules in
-- accounting/bankrules.py, so a user rule can override any default.

CREATE TABLE import_rule (
    company_id  BIGINT      NOT NULL REFERENCES company(company_id),
    rule_id     BIGSERIAL   PRIMARY KEY,
    -- Substring match, or a regular expression when prefixed 're:'.
    pattern     TEXT        NOT NULL,
    direction   TEXT        NOT NULL DEFAULT 'any'
                CHECK (direction IN ('any','debit','credit')),
    account     VARCHAR(8)  NOT NULL,
    tax_code    VARCHAR(8)  NOT NULL DEFAULT '',
    contact     TEXT        NOT NULL DEFAULT '',
    -- TRUE proposes the coding but holds it back from posting. Use it for
    -- anything a person has to decide: a meal that might be entertainment,
    -- a payment to a director that might be a loan.
    review      BOOLEAN     NOT NULL DEFAULT FALSE,
    note        TEXT        NOT NULL DEFAULT '',
    sort_order  INTEGER     NOT NULL DEFAULT 0
);

CREATE INDEX import_rule_order ON import_rule (company_id, sort_order, rule_id);

-- ------------------------------------------------------------- lodgements
-- What has actually been filed, so the calendar stops nagging about it.

CREATE TABLE lodgement (
    company_id   BIGINT      NOT NULL REFERENCES company(company_id),
    kind         TEXT        NOT NULL
                 CHECK (kind IN ('BAS','TPAR','STP','TAX_RETURN','ASIC','SGC')),
    period       TEXT        NOT NULL,          -- 'Q4 FY2026', 'FY2026', '2027'
    lodged_date  DATE        NOT NULL,
    reference    TEXT        NOT NULL DEFAULT '',   -- ATO document ID
    amount       DECIMAL(12,2),
    lodged_by    TEXT        NOT NULL DEFAULT '',
    notes        TEXT        NOT NULL DEFAULT '',
    PRIMARY KEY (company_id, kind, period)
);

-- ------------------------------------------------------ finance schedules

CREATE TABLE finance_schedule (
    company_id   BIGINT      NOT NULL REFERENCES company(company_id),
    account      VARCHAR(8)  NOT NULL,          -- 2800 chattel mortgage, etc.
    principal    DECIMAL(12,2) NOT NULL,
    -- Decimal fraction: 9.3% is 0.093.
    annual_rate  DECIMAL(6,5)  NOT NULL CHECK (annual_rate >= 0 AND annual_rate < 1),
    months       SMALLINT      NOT NULL CHECK (months > 0),
    balloon      DECIMAL(12,2) NOT NULL DEFAULT 0,
    payment      DECIMAL(12,2) NOT NULL,
    first_due    DATE          NOT NULL,
    description  TEXT          NOT NULL DEFAULT '',
    PRIMARY KEY (company_id, account)
);

-- --------------------------------------------------------- chart of accounts
-- Currently a code constant (accounting/accounts.py). Mirrored here so the
-- database is reportable on its own and so a company can add accounts later
-- without a code change.

CREATE TABLE account (
    company_id   BIGINT      NOT NULL REFERENCES company(company_id),
    code         VARCHAR(8)  NOT NULL,
    name         TEXT        NOT NULL,
    type         TEXT        NOT NULL
                 CHECK (type IN ('ASSET','LIABILITY','EQUITY','INCOME',
                                 'COGS','EXPENSE')),
    tax_code     VARCHAR(8)  NOT NULL DEFAULT 'NT',
    role         TEXT        NOT NULL DEFAULT '',
    -- Normal balance is opposite to the type: accumulated depreciation,
    -- dividends paid, discounts given.
    contra       BOOLEAN     NOT NULL DEFAULT FALSE,
    -- FALSE means add it back when working out taxable income.
    deductible   BOOLEAN     NOT NULL DEFAULT TRUE,
    -- Payments here are reportable on the TPAR.
    tpar         BOOLEAN     NOT NULL DEFAULT FALSE,
    note         TEXT        NOT NULL DEFAULT '',
    PRIMARY KEY (company_id, code)
);

ALTER TABLE journal_line
    ADD CONSTRAINT journal_account_exists
    FOREIGN KEY (company_id, account) REFERENCES account (company_id, code);

-- --------------------------------------------------------------- sequences
-- Replaces Table.next_sequence(), which reads the high-water mark and is not
-- safe under concurrency.

CREATE TABLE id_sequence (
    company_id  BIGINT      NOT NULL REFERENCES company(company_id),
    prefix      VARCHAR(8)  NOT NULL,       -- JE, INV, BILL, C, J
    next_value  BIGINT      NOT NULL DEFAULT 1,
    width       SMALLINT    NOT NULL DEFAULT 4,
    PRIMARY KEY (company_id, prefix)
);

CREATE OR REPLACE FUNCTION next_id(p_company BIGINT, p_prefix TEXT)
RETURNS TEXT AS $$
DECLARE
    v_value BIGINT;
    v_width SMALLINT;
BEGIN
    INSERT INTO id_sequence (company_id, prefix, next_value)
        VALUES (p_company, p_prefix, 1)
        ON CONFLICT (company_id, prefix) DO NOTHING;
    UPDATE id_sequence
        SET next_value = next_value + 1
        WHERE company_id = p_company AND prefix = p_prefix
        RETURNING next_value - 1, width INTO v_value, v_width;
    RETURN p_prefix || lpad(v_value::TEXT, v_width, '0');
END;
$$ LANGUAGE plpgsql;

-- ------------------------------------------------------------------- views
-- Convenience only. The engine derives everything itself; these exist so the
-- database can be queried directly by BI tools or an ops console.

CREATE VIEW trial_balance AS
SELECT company_id,
       account,
       sum(debit)  AS total_debit,
       sum(credit) AS total_credit,
       sum(debit) - sum(credit) AS net_debit
FROM journal_line
GROUP BY company_id, account;

CREATE VIEW document_outstanding AS
SELECT d.company_id,
       d.doc_id,
       d.type,
       d.contact_id,
       d.due_date,
       d.total_incl,
       -- Receivables sit on 1100 as a debit, payables on 2000 as a credit.
       CASE WHEN d.type = 'INVOICE'
            THEN coalesce(sum(j.debit - j.credit), 0)
            ELSE coalesce(sum(j.credit - j.debit), 0)
       END AS outstanding
FROM document d
LEFT JOIN journal_line j
       ON j.company_id = d.company_id
      AND j.doc_ref = d.doc_id
      AND j.account = CASE WHEN d.type = 'INVOICE' THEN '1100' ELSE '2000' END
GROUP BY d.company_id, d.doc_id, d.type, d.contact_id, d.due_date, d.total_incl;

COMMIT;
