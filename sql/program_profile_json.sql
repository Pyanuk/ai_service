CREATE TABLE IF NOT EXISTS program_profile_json (
    id BIGSERIAL PRIMARY KEY,
    program_id BIGINT NOT NULL REFERENCES learning_program(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    profile_json JSONB NOT NULL,
    template_name VARCHAR(255),
    generated_docx_path VARCHAR(1000),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
