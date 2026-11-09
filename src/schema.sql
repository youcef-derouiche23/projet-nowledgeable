-- Fichiers déjà traités : sert à éviter les doublons (étape 7)
CREATE TABLE IF NOT EXISTS processed_files (
    id           SERIAL PRIMARY KEY,
    filename     TEXT        NOT NULL UNIQUE,
    file_hash    CHAR(64)    NOT NULL UNIQUE,   -- SHA-256 du contenu
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Transactions individuelles, avec le flag > 5000
CREATE TABLE IF NOT EXISTS transactions (
    id                   SERIAL PRIMARY KEY,
    file_id              INTEGER       NOT NULL REFERENCES processed_files(id) ON DELETE CASCADE,
    datetime_transaction TIMESTAMP     NOT NULL,
    iban_origine         TEXT          NOT NULL,
    pays_source          TEXT          NOT NULL,
    banque_source        TEXT          NOT NULL,
    iban_destinataire    TEXT          NOT NULL,
    pays_destinataire    TEXT          NOT NULL,
    montant              NUMERIC(14,2) NOT NULL,
    flag                 BOOLEAN       NOT NULL
);

-- Résultats des traitements, par fichier traité
CREATE TABLE IF NOT EXISTS sent_by_iban (
    file_id      INTEGER       NOT NULL REFERENCES processed_files(id) ON DELETE CASCADE,
    iban_origine TEXT          NOT NULL,
    total_envoye NUMERIC(14,2) NOT NULL,
    PRIMARY KEY (file_id, iban_origine)
);

CREATE TABLE IF NOT EXISTS sent_by_bank (
    file_id       INTEGER       NOT NULL REFERENCES processed_files(id) ON DELETE CASCADE,
    banque_source TEXT          NOT NULL,
    total_envoye  NUMERIC(14,2) NOT NULL,
    PRIMARY KEY (file_id, banque_source)
);

CREATE TABLE IF NOT EXISTS received_by_iban (
    file_id           INTEGER       NOT NULL REFERENCES processed_files(id) ON DELETE CASCADE,
    iban_destinataire TEXT          NOT NULL,
    total_recu        NUMERIC(14,2) NOT NULL,
    PRIMARY KEY (file_id, iban_destinataire)
);
