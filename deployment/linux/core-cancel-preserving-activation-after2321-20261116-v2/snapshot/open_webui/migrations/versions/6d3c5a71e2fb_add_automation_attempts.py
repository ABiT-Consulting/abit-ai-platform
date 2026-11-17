"""Add durable limits and claim tracking for scheduled automation runs."""

import sqlalchemy as sa
from alembic import op

revision = '6d3c5a71e2fb'
down_revision = 'd4c1a8e37b62'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('automation', sa.Column('attempt_limit', sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('automation', sa.Column('attempts_used', sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('automation_run', sa.Column('lease_until', sa.BigInteger(), nullable=True))
    op.create_index('ix_automation_run_lease', 'automation_run', ['lease_until'])


def downgrade():
    op.drop_index('ix_automation_run_lease', table_name='automation_run')
    op.drop_column('automation_run', 'lease_until')
    op.drop_column('automation', 'attempts_used')
    op.drop_column('automation', 'attempt_limit')
