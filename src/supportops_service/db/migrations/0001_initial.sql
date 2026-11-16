-- 0001_initial: api_credentials, tickets, runs.
-- Source of truth: docs/design/api-004-m02-persistence-foundation.md §3.
-- schema_migrations is owned by the migration runner (bootstrapped before the
-- advisory lock is taken), not by this file.
-- Migrations run inside one transaction: no CREATE INDEX CONCURRENTLY here.

CREATE TABLE api_credentials (
    credential_id uuid PRIMARY KEY,
    tenant_id     text   NOT NULL CHECK (tenant_id ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    key_prefix    text   NOT NULL UNIQUE CHECK (key_prefix ~ '^[a-z2-7]{12}$'),
    key_hash      bytea  NOT NULL CHECK (octet_length(key_hash) = 32),
    pepper_id     text   NOT NULL,
    scopes        text[] NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz,
    revoked_at    timestamptz,
    last_used_at  timestamptz
);

CREATE TABLE tickets (
    ticket_id       text PRIMARY KEY CHECK (ticket_id ~ '^tkt_[0-9a-f]{32}$'),
    tenant_id       text NOT NULL CHECK (tenant_id ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    customer_id     text NOT NULL,
    message         text NOT NULL,
    order_id        text,
    idempotency_key text  NOT NULL,
    request_hash    bytea NOT NULL CHECK (octet_length(request_hash) = 32),
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key),
    UNIQUE (tenant_id, ticket_id)
);

CREATE TABLE runs (
    run_id        text PRIMARY KEY CHECK (run_id ~ '^run_[0-9a-f]{32}$'),
    tenant_id     text NOT NULL,
    ticket_id     text NOT NULL,
    status        text NOT NULL DEFAULT 'accepted'
                  CHECK (status IN ('accepted', 'running', 'completed', 'failed')),
    attempt       int  NOT NULL DEFAULT 0 CHECK (attempt >= 0),   -- fencing token
    max_attempts  int  NOT NULL DEFAULT 1 CHECK (max_attempts >= 1),
    lease_owner   text,
    lease_until   timestamptz,
    answer        text,
    failure_code  text CHECK (failure_code ~ '^[a-z_]{1,64}$'),
    created_at    timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    UNIQUE (ticket_id),
    UNIQUE (tenant_id, run_id),
    FOREIGN KEY (tenant_id, ticket_id) REFERENCES tickets (tenant_id, ticket_id),
    CHECK (status <> 'completed' OR (answer IS NOT NULL AND finished_at IS NOT NULL)),
    CHECK (status <> 'failed'    OR (failure_code IS NOT NULL AND finished_at IS NOT NULL)),
    CHECK (status <> 'running'   OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL AND attempt >= 1)),
    CHECK (status NOT IN ('completed', 'failed') OR (lease_owner IS NULL AND lease_until IS NULL)),
    CHECK (attempt <= max_attempts)
);

CREATE INDEX runs_claim_idx ON runs (created_at) WHERE status = 'accepted';
CREATE INDEX runs_lease_idx ON runs (lease_until) WHERE status = 'running';

-- Defence in depth, independent of DB roles: identity bindings never change.
-- Fixed messages only; no row values are echoed.
CREATE FUNCTION tickets_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'tickets rows are immutable' USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE TRIGGER tickets_no_update
    BEFORE UPDATE ON tickets
    FOR EACH ROW EXECUTE FUNCTION tickets_append_only();

CREATE FUNCTION runs_guard_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.ticket_id IS DISTINCT FROM OLD.ticket_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'run binding is immutable' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status IN ('completed', 'failed') THEN
        RAISE EXCEPTION 'terminal run is immutable' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.attempt < OLD.attempt THEN
        RAISE EXCEPTION 'attempt must not decrease' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER runs_guard
    BEFORE UPDATE ON runs
    FOR EACH ROW EXECUTE FUNCTION runs_guard_update();
