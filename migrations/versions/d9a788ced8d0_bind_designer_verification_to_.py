"""bind designer verification to specification hash"""
from alembic import op, context
import sqlalchemy as sa

revision = 'd9a788ced8d0'
down_revision = '7457427985fd'
branch_labels = None
depends_on = None

def upgrade():
    # Batch mode supports SQLite as well as PostgreSQL. Existence checks also
    # permit recovery after an interrupted nontransactional SQLite ALTER.
    columns = set()
    if not context.is_offline_mode() and op.get_bind().dialect.name == 'sqlite':
        columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('revisions')}
    with op.batch_alter_table('revisions') as batch:
        if 'verified_by' not in columns:
            batch.add_column(sa.Column('verified_by',sa.String(36),nullable=True))
        if 'verified_spec_hash' not in columns:
            batch.add_column(sa.Column('verified_spec_hash',sa.String(64),nullable=True))
        batch.create_foreign_key('fk_revisions_verified_by','users',['verified_by'],['id'])

def downgrade():
    with op.batch_alter_table('revisions') as batch:
        batch.drop_constraint('fk_revisions_verified_by',type_='foreignkey')
        batch.drop_column('verified_spec_hash')
        batch.drop_column('verified_by')
