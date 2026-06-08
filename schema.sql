-- Schema for the Asterisk hangup manager contact table.
--
-- When a hangup is detected, the service looks up a row whose `dst`
-- matches the configured AMI event field (default: the dialed extension)
-- and emails the address stored in `email`.

CREATE TABLE IF NOT EXISTS hangup_contacts (
    dst         VARCHAR(64)  NOT NULL,
    email       VARCHAR(255) NOT NULL,
    description VARCHAR(255) NOT NULL DEFAULT '',
    PRIMARY KEY (dst)
);

-- Example rows:
-- INSERT INTO hangup_contacts (dst, email, description) VALUES
--     ('1001', 'reception@example.com', 'Front desk extension'),
--     ('2000', 'support@example.com',   'Support queue');
