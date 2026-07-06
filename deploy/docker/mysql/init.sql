CREATE DATABASE IF NOT EXISTS super_agent;
USE super_agent;

CREATE TABLE IF NOT EXISTS memories (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id     VARCHAR(64) NOT NULL,
    session_id  VARCHAR(64),
    `key`       VARCHAR(255) NOT NULL,
    value       TEXT NOT NULL,
    metadata    JSON,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_user_key (user_id, `key`),
    INDEX idx_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id         VARCHAR(64) NOT NULL,
    query_text      TEXT NOT NULL,
    num_chunks      INT DEFAULT 0,
    chunk_ids       JSON,
    answer_text     MEDIUMTEXT,
    num_citations   INT DEFAULT 0,
    latency_ms      INT DEFAULT 0,
    status          VARCHAR(32) DEFAULT 'success',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_created (created_at),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
