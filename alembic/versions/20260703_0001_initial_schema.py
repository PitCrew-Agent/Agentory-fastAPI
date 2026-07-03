"""initial schema, equipment·knowledge·chat (DEV_DATABASE, DEV_VECTORDB)

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-03

pgvector 확장 생성 후 정형·벡터·대화 테이블 일괄 생성
embedding HNSW 인덱스는 vector_cosine_ops 사용
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "equipment_master",
        sa.Column("equipment_id", sa.String(50), primary_key=True),
        sa.Column("line_name", sa.String(50), nullable=False),
        sa.Column("process_type", sa.String(50), nullable=False),
        sa.Column("location", sa.String(50)),
        sa.Column("manager_dept", sa.String(50)),
    )

    op.create_table(
        "equipment_telemetry",
        sa.Column("log_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "equipment_id",
            sa.String(50),
            sa.ForeignKey("equipment_master.equipment_id"),
            nullable=False,
        ),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("temperature", sa.Numeric(5, 2)),
        sa.Column("pressure", sa.Numeric(5, 2)),
        sa.Column("rf_power", sa.Numeric(6, 2)),
        sa.Column("gas_flow", sa.Numeric(7, 2)),
        sa.Column("alarm_code", sa.String(20)),
    )
    op.create_index(
        "ix_telemetry_equipment_time", "equipment_telemetry", ["equipment_id", "timestamp"]
    )

    op.create_table(
        "knowledge_collection",
        sa.Column("chunk_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("doc_id", sa.String(50), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("equipment_type", sa.String(50)),
        sa.Column("alarm_code", sa.String(20)),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_knowledge_embedding_hnsw",
        "knowledge_collection",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_knowledge_equipment_type", "knowledge_collection", ["equipment_type"])
    op.create_index("ix_knowledge_alarm_code", "knowledge_collection", ["alarm_code"])

    op.create_table(
        "chat_session",
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_sub", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "chat_message",
        sa.Column("message_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_session.session_id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("trace", postgresql.JSONB),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')", name="ck_chat_message_role"
        ),
    )
    op.create_index("ix_chat_message_session_time", "chat_message", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_table("chat_message")
    op.drop_table("chat_session")
    op.drop_table("knowledge_collection")
    op.drop_table("equipment_telemetry")
    op.drop_table("equipment_master")
    op.execute("DROP EXTENSION IF EXISTS vector")
