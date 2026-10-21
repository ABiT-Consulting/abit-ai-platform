"""Add isolated pending email registrations; no existing user data changed.

Revision ID: a6f3c2d9e1b0
Revises: d4c1a8e37b62
"""
from alembic import op
import sqlalchemy as sa

revision = 'a6f3c2d9e1b0'
down_revision = 'd4c1a8e37b62'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'pending_email_signup',
        sa.Column('email', sa.String(), primary_key=True),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('profile_image_url', sa.Text(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False, unique=True),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.Column('sent_at', sa.BigInteger(), nullable=False),
    )


def downgrade():
    op.drop_table('pending_email_signup')