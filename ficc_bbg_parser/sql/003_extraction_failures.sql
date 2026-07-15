SET search_path = ficc, public;

ALTER TABLE ingestion_run
    ADD COLUMN IF NOT EXISTS extraction_failure_count INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'ficc.ingestion_run'::regclass
          AND conname = 'ingestion_run_failure_count_check'
    ) THEN
        ALTER TABLE ingestion_run
            ADD CONSTRAINT ingestion_run_failure_count_check
            CHECK (extraction_failure_count >= 0);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS quote_extraction_failure (
    failure_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL,
    quote_line_pk       BIGINT NOT NULL,
    model               VARCHAR(100) NOT NULL,
    attempts            SMALLINT NOT NULL,
    error_type          VARCHAR(100) NOT NULL,
    error_message       TEXT NOT NULL,
    failure_payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT quote_extraction_failure_line_fk
        FOREIGN KEY (run_id, quote_line_pk)
        REFERENCES quote_line(run_id, quote_line_pk)
        ON DELETE CASCADE,
    CONSTRAINT quote_extraction_failure_run_line_unique
        UNIQUE (run_id, quote_line_pk),
    CONSTRAINT quote_extraction_failure_attempts_check
        CHECK (attempts > 0)
);

CREATE INDEX IF NOT EXISTS quote_extraction_failure_run_idx
    ON quote_extraction_failure (run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS quote_extraction_failure_error_idx
    ON quote_extraction_failure (error_type, created_at DESC);

INSERT INTO schema_migration (version)
VALUES ('003_extraction_failures')
ON CONFLICT (version) DO NOTHING;
