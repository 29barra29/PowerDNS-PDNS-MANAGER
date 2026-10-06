-- PDNS Manager 2.4.1 - Datenbankschema (nur Struktur, keine Daten).
-- Ausgangspunkt fuer tests/test_migrations_db.py (Upgrade 2.4.1 -> 3.0). tests/dbutil.py::load_schema_241
-- fuehrt nur die CREATE-TABLE-Statements dieser Datei aus. Nicht von Hand aendern - neu erzeugen.
--
-- Quelle: Tag v2.4.1, init_db() des 2.4.1-Codes gegen eine leere mariadb:11-Datenbank (scripts/dev/test-db.sh),
-- danach mariadb-dump --no-data; behalten wurden nur die CREATE-TABLE-Bloecke.
-- Die Tabellen-Optionen "DEFAULT CHARSET=... COLLATE=..." wurden entfernt: 2.4.1 legte die Tabellen ueber
-- create_all ohne Collation-Vorgabe an, sie erben die Vorgabe der Datenbank. Mit gepinnter Collation
-- (unicode_ci aus test-db.sh) weicht das Upgrade-Schema auf Servern mit anderer Vorgabe (GitHub-CI:
-- utf8mb4_uca1400_ai_ci) von der Neuinstallation ab (test_upgraded_schema_equals_fresh_install).
-- Die JSON-Spalten (longtext ... utf8mb4_bin) bleiben, so legt sie SQLAlchemy auch bei Neuinstallation an.
-- Erzeugt mit (Repo-Wurzel, Testimage pdnsmgr-test:w0; das Root-Passwort steht in scripts/dev/test-db.sh):
--
--   D=$(mktemp -d) && git archive v2.4.1 VERSION backend | tar -x -C "$D"
--   scripts/dev/test-db.sh up && scripts/dev/test-db.sh create schema_241
--   scripts/dev/with-slot.sh docker run --rm -i --network pdnsmgr-test-net -v "$D/backend:/src:ro" \
--     -e JWT_SECRET_KEY=x -e DATABASE_URL="$(scripts/dev/test-db.sh url schema_241)" pdnsmgr-test:w0 \
--     bash -c 'cp -r /src /work/backend && cd /work/backend && python -' <<'EOF'
--   import asyncio, app.models.models
--   from app.core.database import init_db, engine
--   async def main():
--       await init_db()
--       await engine.dispose()
--   asyncio.run(main())
--   EOF
--   docker exec -e MYSQL_PWD="$ROOT_PW" pdnsmgr-test-db mariadb-dump -uroot --no-data --skip-comments \
--     --compact --skip-dump-date --skip-add-drop-table schema_241 \
--     | python3 -c 'import re,sys; print("\n".join(m.group(0) for m in re.finditer(r"CREATE TABLE .*?;\n", sys.stdin.read(), re.S)), end="")' \
--     > backend/tests/fixtures/schema_241.body.sql   # anschliessend unter diesen Kopf gesetzt
--   scripts/dev/test-db.sh drop schema_241; rm -rf "$D"
--
-- Stand der Erzeugung: v2.4.1 (6b6a3a3), mariadb-dump 11.8.6, 10 Tabellen.

CREATE TABLE `acme_tokens` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `token_prefix` varchar(16) NOT NULL,
  `token_hash` varchar(128) NOT NULL,
  `allowed_zones` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL CHECK (json_valid(`allowed_zones`)),
  `created_by_id` int(11) DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `last_used_at` datetime DEFAULT NULL,
  `last_used_ip` varchar(64) DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_acme_tokens_token_hash` (`token_hash`),
  KEY `ix_acme_tokens_token_prefix` (`token_prefix`)
) ENGINE=InnoDB;

CREATE TABLE `audit_logs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `timestamp` datetime NOT NULL,
  `action` varchar(50) NOT NULL,
  `resource_type` varchar(50) NOT NULL,
  `resource_name` varchar(255) DEFAULT NULL,
  `server_name` varchar(100) DEFAULT NULL,
  `details` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`details`)),
  `status` varchar(20) DEFAULT NULL,
  `error_message` text DEFAULT NULL,
  `user_id` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;

CREATE TABLE `panel_tokens` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` int(11) NOT NULL,
  `name` varchar(100) NOT NULL,
  `token_prefix` varchar(20) NOT NULL,
  `token_hash` varchar(128) NOT NULL,
  `created_at` datetime NOT NULL,
  `last_used_at` datetime DEFAULT NULL,
  `last_used_ip` varchar(64) DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_panel_tokens_token_hash` (`token_hash`),
  KEY `ix_panel_tokens_user_id` (`user_id`),
  KEY `ix_panel_tokens_token_prefix` (`token_prefix`)
) ENGINE=InnoDB;

CREATE TABLE `server_configs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `display_name` varchar(255) DEFAULT NULL,
  `url` varchar(500) NOT NULL,
  `api_key` varchar(500) NOT NULL,
  `description` text DEFAULT NULL,
  `is_active` tinyint(1) DEFAULT NULL,
  `allow_writes` tinyint(1) DEFAULT NULL,
  `sort_order` int(11) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB;

CREATE TABLE `system_settings` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `key` varchar(100) NOT NULL,
  `value` text DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_system_settings_key` (`key`)
) ENGINE=InnoDB;

CREATE TABLE `user_zone_access` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` int(11) NOT NULL,
  `zone_name` varchar(255) NOT NULL,
  `permission` varchar(20) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_user_zone_access_user_id` (`user_id`),
  KEY `ix_user_zone_access_zone_name` (`zone_name`)
) ENGINE=InnoDB;

CREATE TABLE `users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `username` varchar(100) NOT NULL,
  `email` varchar(255) DEFAULT NULL,
  `hashed_password` varchar(255) NOT NULL,
  `display_name` varchar(255) DEFAULT NULL,
  `role` varchar(20) NOT NULL,
  `is_active` tinyint(1) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `last_login` datetime DEFAULT NULL,
  `phone` varchar(50) DEFAULT NULL,
  `company` varchar(255) DEFAULT NULL,
  `street` varchar(255) DEFAULT NULL,
  `postal_code` varchar(20) DEFAULT NULL,
  `city` varchar(100) DEFAULT NULL,
  `country` varchar(100) DEFAULT NULL,
  `date_of_birth` datetime DEFAULT NULL,
  `preferred_language` varchar(10) DEFAULT NULL,
  `totp_enabled` tinyint(1) NOT NULL,
  `totp_secret` varchar(64) DEFAULT NULL,
  `totp_pending_secret` varchar(64) DEFAULT NULL,
  `webauthn_user_handle` varchar(64) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_users_username` (`username`),
  UNIQUE KEY `email` (`email`)
) ENGINE=InnoDB;

CREATE TABLE `webauthn_credentials` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` int(11) NOT NULL,
  `name` varchar(100) NOT NULL,
  `credential_id` varchar(512) NOT NULL,
  `public_key` text NOT NULL,
  `sign_count` int(11) NOT NULL,
  `transports` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`transports`)),
  `aaguid` varchar(64) DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `last_used_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_webauthn_credentials_credential_id` (`credential_id`),
  KEY `ix_webauthn_credentials_user_id` (`user_id`)
) ENGINE=InnoDB;

CREATE TABLE `webhooks` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` int(11) NOT NULL,
  `name` varchar(100) NOT NULL,
  `url` varchar(1024) NOT NULL,
  `secret` varchar(256) NOT NULL,
  `events` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL CHECK (json_valid(`events`)),
  `is_active` tinyint(1) NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_webhooks_user_id` (`user_id`)
) ENGINE=InnoDB;

CREATE TABLE `zone_templates` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `description` text DEFAULT NULL,
  `records` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL CHECK (json_valid(`records`)),
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB;
