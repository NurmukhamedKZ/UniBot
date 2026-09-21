-- Последнее увиденное состояние wsp.kbtu.kz «Отметка посещения»: уведомляем только
-- при переходе закрыто -> открыто, а не на каждой проверке.
ALTER TABLE users ADD COLUMN wsp_open boolean NOT NULL DEFAULT false;
