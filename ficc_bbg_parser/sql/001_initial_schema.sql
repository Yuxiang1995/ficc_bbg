CREATE SCHEMA IF NOT EXISTS ficc AUTHORIZATION ficc_app;
SET search_path = ficc, public;

CREATE TABLE IF NOT EXISTS schema_migration (
    version             VARCHAR(50) PRIMARY KEY,
    applied_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_run (
    run_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_mode         VARCHAR(20) NOT NULL,
    source_date         DATE,
    status              VARCHAR(20) NOT NULL DEFAULT 'running',
    raw_record_count    INTEGER NOT NULL DEFAULT 0,
    paragraph_count     INTEGER NOT NULL DEFAULT 0,
    quote_line_count    INTEGER NOT NULL DEFAULT 0,
    quote_count         INTEGER NOT NULL DEFAULT 0,
    splitter_model      VARCHAR(100),
    extractor_model     VARCHAR(100),
    parser_version      VARCHAR(50),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    error_message       TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT ingestion_run_source_mode_check
        CHECK (source_mode IN ('txt', 'sqlserver', 'manual', 'other')),
    CONSTRAINT ingestion_run_status_check
        CHECK (status IN ('running', 'completed', 'failed')),
    CONSTRAINT ingestion_run_counts_check
        CHECK (
            raw_record_count >= 0
            AND paragraph_count >= 0
            AND quote_line_count >= 0
            AND quote_count >= 0
        )
);

CREATE TABLE IF NOT EXISTS chat_email (
    email_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES ingestion_run(run_id) ON DELETE CASCADE,
    source_system       VARCHAR(50) NOT NULL DEFAULT 'sqlserver',
    source_record_key   VARCHAR(200),
    subject             TEXT,
    content             TEXT NOT NULL,
    receive_time        TIMESTAMPTZ,
    content_hash        CHAR(64) NOT NULL,
    raw_payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chat_email_source_record_unique
        UNIQUE (source_system, source_record_key),
    CONSTRAINT chat_email_run_hash_unique
        UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS chat_paragraph (
    paragraph_pk        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES ingestion_run(run_id) ON DELETE CASCADE,
    email_id            BIGINT REFERENCES chat_email(email_id) ON DELETE SET NULL,
    paragraph_id        VARCHAR(100) NOT NULL,
    paragraph_type      VARCHAR(30) NOT NULL,
    text                TEXT NOT NULL,
    quote_time          TIMESTAMPTZ,
    quote_source        TEXT,
    speaker             TEXT,
    source_date         DATE,
    confidence          NUMERIC(6, 5) NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chat_paragraph_run_id_unique UNIQUE (run_id, paragraph_id),
    CONSTRAINT chat_paragraph_run_pk_unique UNIQUE (run_id, paragraph_pk),
    CONSTRAINT chat_paragraph_type_check
        CHECK (paragraph_type IN ('quote', 'axes', 'primary', 'stats', 'commentary', 'noise')),
    CONSTRAINT chat_paragraph_confidence_check
        CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE TABLE IF NOT EXISTS quote_line (
    quote_line_pk       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES ingestion_run(run_id) ON DELETE CASCADE,
    paragraph_pk        BIGINT NOT NULL,
    line_id             VARCHAR(100) NOT NULL,
    source_line_no      INTEGER,
    text                TEXT NOT NULL,
    quote_time          TIMESTAMPTZ,
    quote_source        TEXT,
    speaker             TEXT,
    source_date         DATE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT quote_line_run_id_unique UNIQUE (run_id, line_id),
    CONSTRAINT quote_line_run_pk_unique UNIQUE (run_id, quote_line_pk),
    CONSTRAINT quote_line_paragraph_fk
        FOREIGN KEY (run_id, paragraph_pk)
        REFERENCES chat_paragraph(run_id, paragraph_pk)
        ON DELETE CASCADE,
    CONSTRAINT quote_line_source_line_no_check
        CHECK (source_line_no IS NULL OR source_line_no > 0)
);

CREATE TABLE IF NOT EXISTS bond_security (
    security_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    isin                VARCHAR(12) UNIQUE,
    ticker              VARCHAR(50),
    issuer              TEXT,
    currency            VARCHAR(3),
    maturity_date       DATE,
    coupon_type         VARCHAR(10),
    coupon_rate         NUMERIC(12, 8),
    rating_moodys       VARCHAR(20),
    rating_sp           VARCHAR(20),
    rating_fitch        VARCHAR(20),
    region              VARCHAR(50),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bond_security_coupon_type_check
        CHECK (coupon_type IS NULL OR coupon_type IN ('Fixed', 'FRN'))
);

CREATE TABLE IF NOT EXISTS bond_quote (
    quote_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL,
    quote_line_pk       BIGINT NOT NULL UNIQUE,
    security_id         BIGINT REFERENCES bond_security(security_id) ON DELETE SET NULL,
    dedupe_key          CHAR(64) UNIQUE,

    ticker              VARCHAR(50),
    isin                VARCHAR(12),
    issuer              TEXT,
    currency            VARCHAR(3),
    maturity_text       VARCHAR(20),
    maturity_date       DATE,
    maturity_precision  VARCHAR(10),
    maturity_year       SMALLINT,
    maturity_month      SMALLINT,
    maturity_day        SMALLINT,
    coupon_type         VARCHAR(10),
    coupon_rate         NUMERIC(12, 8),
    rating_raw          TEXT,
    rating_moodys       VARCHAR(20),
    rating_sp           VARCHAR(20),
    rating_fitch        VARCHAR(20),

    bid_price           NUMERIC(18, 8),
    ask_price           NUMERIC(18, 8),
    bid_spread          NUMERIC(14, 6),
    ask_spread          NUMERIC(14, 6),
    spread_bid_ask      NUMERIC(14, 6),
    spread_unit         VARCHAR(20),
    bid_size            NUMERIC(20, 4),
    ask_size            NUMERIC(20, 4),
    size_unit           VARCHAR(20),

    quote_time          TIMESTAMPTZ,
    quote_source        TEXT,
    quote_type          VARCHAR(20),
    bond_type           VARCHAR(20),
    region              VARCHAR(50),
    remark              TEXT,
    message_id          VARCHAR(100),
    message_type        VARCHAR(30),
    source_line_no      INTEGER,
    raw_line            TEXT NOT NULL,
    parser_name         VARCHAR(50),
    parse_status        VARCHAR(20) NOT NULL DEFAULT 'parsed',
    confidence          NUMERIC(6, 5) NOT NULL DEFAULT 0,
    raw_payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT bond_quote_line_fk
        FOREIGN KEY (run_id, quote_line_pk)
        REFERENCES quote_line(run_id, quote_line_pk)
        ON DELETE CASCADE,
    CONSTRAINT bond_quote_maturity_precision_check
        CHECK (maturity_precision IS NULL OR maturity_precision IN ('year', 'month', 'day')),
    CONSTRAINT bond_quote_maturity_year_check
        CHECK (maturity_year IS NULL OR maturity_year BETWEEN 1900 AND 2200),
    CONSTRAINT bond_quote_maturity_month_check
        CHECK (maturity_month IS NULL OR maturity_month BETWEEN 1 AND 12),
    CONSTRAINT bond_quote_maturity_day_check
        CHECK (maturity_day IS NULL OR maturity_day BETWEEN 1 AND 31),
    CONSTRAINT bond_quote_coupon_type_check
        CHECK (coupon_type IS NULL OR coupon_type IN ('Fixed', 'FRN')),
    CONSTRAINT bond_quote_spread_unit_check
        CHECK (spread_unit IS NULL OR spread_unit IN ('bp', 'price_point')),
    CONSTRAINT bond_quote_type_check
        CHECK (quote_type IS NULL OR quote_type IN ('price', 'spread', 'yield', 'offer', 'bid')),
    CONSTRAINT bond_quote_bond_type_check
        CHECK (bond_type IS NULL OR bond_type IN ('Primary', 'Secondary')),
    CONSTRAINT bond_quote_message_type_check
        CHECK (message_type IS NULL OR message_type IN ('quote_single', 'quote_list', 'unknown')),
    CONSTRAINT bond_quote_parse_status_check
        CHECK (parse_status IN ('parsed', 'needs_review', 'skipped')),
    CONSTRAINT bond_quote_confidence_check
        CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS chat_email_receive_time_idx
    ON chat_email (receive_time);
CREATE INDEX IF NOT EXISTS chat_paragraph_run_type_idx
    ON chat_paragraph (run_id, paragraph_type);
CREATE INDEX IF NOT EXISTS quote_line_paragraph_idx
    ON quote_line (paragraph_pk);
CREATE INDEX IF NOT EXISTS bond_security_ticker_maturity_idx
    ON bond_security (ticker, maturity_date);
CREATE INDEX IF NOT EXISTS bond_quote_quote_time_idx
    ON bond_quote (quote_time);
CREATE INDEX IF NOT EXISTS bond_quote_ticker_time_idx
    ON bond_quote (ticker, quote_time DESC);
CREATE INDEX IF NOT EXISTS bond_quote_isin_time_idx
    ON bond_quote (isin, quote_time DESC)
    WHERE isin IS NOT NULL;
CREATE INDEX IF NOT EXISTS bond_quote_source_time_idx
    ON bond_quote (quote_source, quote_time DESC);
CREATE INDEX IF NOT EXISTS bond_quote_review_idx
    ON bond_quote (parse_status, confidence)
    WHERE parse_status = 'needs_review';
CREATE INDEX IF NOT EXISTS bond_quote_raw_payload_idx
    ON bond_quote USING GIN (raw_payload);

INSERT INTO schema_migration (version)
VALUES ('001_initial_schema')
ON CONFLICT (version) DO NOTHING;
