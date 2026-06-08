-- Schema for the Asterisk hangup manager contact table.
--
-- When a missed call is detected (the call's originating channel hangs up
-- without any dial/queue attempt creating an outgoing channel), the service
-- looks up a row whose `dst` matches the configured AMI event field
-- (default: the dialed extension) and emails the address stored in `email`.
-- The `email` column may be NULL/empty: in that case the service falls back
-- to the address configured in `SMTP_FALLBACK_EMAIL`.

CREATE TABLE IF NOT EXISTS hangup_contacts (
    dst         VARCHAR(64)  NOT NULL,
    email       VARCHAR(255) NULL,
    description VARCHAR(255) NOT NULL DEFAULT '',
    PRIMARY KEY (dst)
);

-- Example rows:
-- INSERT INTO hangup_contacts (dst, email, description) VALUES
--     ('1001', 'reception@example.com', 'Front desk extension'),
--     ('2000', 'support@example.com',   'Support queue'),
--     ('5555555', NULL,                 'No mailbox: use fallback recipient');
