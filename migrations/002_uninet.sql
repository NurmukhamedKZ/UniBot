-- Переезд с UniX на uninet.kbtu.kz: креды теперь от KBTU (Keycloak).
-- Сохранённые логины/пароли UniX для uninet не подходят — обнуляем.
ALTER TABLE users RENAME COLUMN unix_email TO kbtu_email;
ALTER TABLE users RENAME COLUMN unix_password_enc TO kbtu_password_enc;
UPDATE users SET kbtu_email = NULL, kbtu_password_enc = NULL;
