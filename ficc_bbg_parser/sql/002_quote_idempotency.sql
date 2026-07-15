SET search_path = ficc, public;

ALTER TABLE chat_email
    DROP CONSTRAINT IF EXISTS chat_email_source_record_unique;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'ficc.chat_email'::regclass
          AND conname = 'chat_email_run_source_record_unique'
    ) THEN
        ALTER TABLE chat_email
            ADD CONSTRAINT chat_email_run_source_record_unique
            UNIQUE (run_id, source_system, source_record_key);
    END IF;
END
$$;

ALTER TABLE bond_quote
    ADD COLUMN IF NOT EXISTS quote_sequence INTEGER NOT NULL DEFAULT 1;

ALTER TABLE bond_quote
    DROP CONSTRAINT IF EXISTS bond_quote_quote_line_pk_key;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'ficc.bond_quote'::regclass
          AND conname = 'bond_quote_line_sequence_unique'
    ) THEN
        ALTER TABLE bond_quote
            ADD CONSTRAINT bond_quote_line_sequence_unique
            UNIQUE (run_id, quote_line_pk, quote_sequence);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'ficc.bond_quote'::regclass
          AND conname = 'bond_quote_sequence_check'
    ) THEN
        ALTER TABLE bond_quote
            ADD CONSTRAINT bond_quote_sequence_check
            CHECK (quote_sequence > 0);
    END IF;
END
$$;

INSERT INTO schema_migration (version)
VALUES ('002_quote_idempotency')
ON CONFLICT (version) DO NOTHING;
